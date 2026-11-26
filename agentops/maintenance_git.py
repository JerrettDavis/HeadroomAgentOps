"""Pinned mechanical rebases; Git hooks and repository configuration never execute."""
import hashlib
import re
import uuid
from pathlib import Path

from .process import command
from .stack_git import GitStack


class MaintenanceGit(GitStack):
    def _sha(self, value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{40,64}', value):
            raise ValueError('Expected an immutable commit ID')
        return value

    def contribution(self, checkout, base, head):
        patch = self.git(['diff', '--no-ext-diff', '--no-textconv', '--binary', base, head], checkout)
        # Stable patch ID ignores hunk location and whitespace, so use a second
        # path/mode/content-line digest to preserve whitespace-only author edits.
        meaningful, in_hunk, binary = [], False, False
        for line in patch.splitlines():
            if line.startswith('diff --git '):
                in_hunk, binary = False, False
                meaningful.append(line)
            elif line.startswith('@@'):
                in_hunk = True
            elif line == 'GIT binary patch':
                binary = True
                meaningful.append(line)
            elif binary or (in_hunk and line.startswith(('+', '-'))):
                meaningful.append(line)
            elif not in_hunk and line.startswith(('old mode ', 'new mode ', 'new file mode ',
                    'deleted file mode ', 'rename from ', 'rename to ', 'copy from ', 'copy to ')):
                meaningful.append(line)
        return hashlib.sha256('\n'.join(meaningful).encode()).hexdigest()

    def prepare(self, record):
        head, main = self._sha(record['head_sha']), self._sha(record['main_sha'])
        if type(record['number']) is not int or record['number'] <= 0:
            raise ValueError('Invalid PR number')
        checkout = self.root / str(uuid.uuid4()) / 'repo'
        checkout.mkdir(parents=True)
        self.git(['init', str(checkout)], checkout.parent)
        self.git(['fetch', '--no-tags', '--', self.remote,
                  'refs/heads/main:refs/maintenance/main',
                  f'refs/pull/{record["number"]}/head:refs/maintenance/head'], checkout)
        for ref, sha in [('main', main), ('head', head)]:
            if self.git(['rev-parse', f'refs/maintenance/{ref}'], checkout) != sha:
                raise ValueError('PR head or main changed during fetch; refresh and retry')
        base = self.git(['merge-base', main, head], checkout)
        if self.git(['rev-list', '--merges', f'{base}..{head}'], checkout):
            raise ValueError('Merge commits need explicit agent remediation')
        before = self.contribution(checkout, base, head)
        self.git(['checkout', '--detach', head], checkout)
        self.git(['config', 'user.name', 'Headroom AgentOps'], checkout)
        self.git(['config', 'user.email', 'agentops@users.noreply.github.com'], checkout)
        try:
            self.git(['rebase', '--onto', main, base], checkout)
        except RuntimeError as error:
            self.git(['rebase', '--abort'], checkout)
            raise ValueError('Rebase conflict; send to remediation or resolve manually') from error
        new = self.git(['rev-parse', 'HEAD'], checkout)
        after = self.contribution(checkout, main, new)
        if before != after:
            raise ValueError('Contribution changed during rebase; explicit remediation required')
        return {'checkout': str(checkout), 'new_head': new, 'base_sha': base,
                'contribution': before, 'equivalent': True}

    def remote_for(self, record):
        if self.remote != f'https://github.com/{self.repo}.git':
            return self.remote  # Local deterministic fixtures.
        repo = record.get('head_repo')
        if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError('Fork is unavailable')
        return f'https://github.com/{repo}.git'

    def publish(self, record):
        checkout = Path(record['checkout']).resolve()
        if not checkout.is_relative_to(self.root.resolve()):
            raise ValueError('Checkout outside owned maintenance directory')
        branch = record['head_ref']
        self.git(['check-ref-format', 'refs/heads/' + branch], checkout)
        old, new = self._sha(record['head_sha']), self._sha(record['new_head'])
        self.git(['merge-base', '--is-ancestor', record['main_sha'], new], checkout)
        remote = self.remote_for(record)
        observed = self.git(['ls-remote', '--heads', '--', remote, 'refs/heads/' + branch], checkout)
        current = observed.split()[0] if observed else None
        if current == new:
            return {'new_head': new, 'reconciled': True}
        if current != old:
            raise ValueError('PR head changed; lease rejected')
        self.git(['push', f'--force-with-lease=refs/heads/{branch}:{old}', '--', remote,
                  f'{new}:refs/heads/{branch}'], checkout)
        return {'new_head': new}
