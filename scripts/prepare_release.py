"""Prepare version-matched public install instructions, config and checksums."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib


def prepare_release(root, dist, repository, tag=None):
    root, dist = Path(root), Path(dist)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Repository must be owner/name')
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    tag = tag or 'v' + version
    if tag != 'v' + version:
        raise ValueError('Release tag must match project version')
    wheel = f'headroom_agentops-{version}-py3-none-any.whl'
    source = f'headroom_agentops-{version}.tar.gz'
    if not (dist / wheel).is_file() or not (dist / source).is_file():
        raise ValueError('Build the matching wheel and source distribution first')
    image = f'ghcr.io/{repository.lower()}/worker:{tag}'
    template = (root / 'agentops.example.toml').read_text()
    config = re.sub(r'^repo = .*$', 'repo = "OWNER/REPO"', template, flags=re.MULTILINE)
    config = re.sub(r'^image = .*$', f'image = "{image}"', config, flags=re.MULTILINE)
    parsed = tomllib.loads(config)
    if parsed.get('image') != image or parsed.get('repo') != 'OWNER/REPO' or parsed.get('publish') is not False:
        raise ValueError('Release example must select the matching image and disable publication')
    (dist / 'agentops.example.toml').write_text(config, encoding='utf8', newline='\n')
    url = f'https://github.com/{repository}/releases/download/{tag}'
    guide = f'''# Install Headroom AgentOps {version}

Requires Python 3.12 or newer. Install the coordinator with either uv or pipx:

```sh
uv tool install "{url}/{wheel}"
```

```sh
pipx install "{url}/{wheel}"
```

The same commands work in PowerShell. Alternatively install the wheel into a
Python virtual environment with `python -m pip install "{url}/{wheel}"`.
Verify installation with `headroom-review --help`. No source checkout is required.

## Set up a workspace

Review workers additionally need Git, GitHub CLI, Docker with Linux containers,
and model authentication. The worker image currently targets Linux amd64;
other architectures can build it from source. Run the Linux coordinator as a
non-root user with a non-root primary group.

```sh
docker pull {image}
gh auth login
gh release download {tag} --repo {repository} --pattern agentops.example.toml
```

The release also provides a compressed worker image archive for direct download
or offline transfer. It loads the same versioned image tag used by the config:

```sh
gh release download {tag} --repo {repository} --pattern worker-image-linux-amd64.tar.gz
docker load --input worker-image-linux-amd64.tar.gz
```

Copy `agentops.example.toml` to `agentops.toml` in your chosen working directory.
Set `repo` to the repository you want reviewed, choose the harness and configure
its model credentials as described in the repository README. The image is already
pinned to this release. Publication defaults to false.

```sh
headroom-review doctor
headroom-review run --limit 1 --no-publish
headroom-review status --details
headroom-review panel
```

The panel is served at http://127.0.0.1:8765 and reads the local ledger. Keep
`agentops.toml`, `.agentops/` and model authentication local. The worker image is
an internal component launched by the coordinator, not a standalone review server.

## Integrity and upgrades

Download `SHA256SUMS` and the desired release assets. On Linux use
`sha256sum --check SHA256SUMS` with all listed assets in one directory; on
PowerShell compare `Get-FileHash -Algorithm SHA256 <asset>` with the listed digest.
The checksum file covers the wheel, source distribution, example config and this
guide and worker image archive. For independent release provenance, inspect the
linked GitHub Actions run.

For an upgrade, install the new release wheel with `uv tool install --force <URL>`
or `pipx install --force <URL>`, pull its versioned image and update only the image
setting in your existing configuration. Preserve your state and credentials.
'''
    (dist / 'INSTALL.md').write_text(guide, encoding='utf8', newline='\n')
    assets = [wheel, source, 'agentops.example.toml', 'INSTALL.md']
    sums = ''.join(f'{hashlib.sha256((dist / name).read_bytes()).hexdigest()}  {name}\n'
                   for name in sorted(assets))
    (dist / 'SHA256SUMS').write_text(sums, encoding='utf8', newline='\n')
    return {'version': version, 'tag': tag, 'image': image, 'wheel_url': f'{url}/{wheel}'}


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY'))
    parser.add_argument('--tag')
    parser.add_argument('--dist', default='dist')
    args = parser.parse_args()
    repository = args.repository or json.loads((root / '.github/repository.json').read_text())['repository']
    tag = args.tag or (os.environ.get('GITHUB_REF_NAME') if os.environ.get('GITHUB_REF_TYPE') == 'tag' else None)
    release = prepare_release(root, args.dist, repository, tag)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf8') as output:
            print('image=' + release['image'], file=output)
    print(json.dumps(release))


if __name__ == '__main__':
    main()
