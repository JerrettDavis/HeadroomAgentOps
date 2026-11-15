# Headroom AgentOps identity

The mark is an ivory H under a mint ceiling, with a little space between them.
It represents room to think and ship. The dark tile gives it a stable silhouette
on light and dark surfaces, including the Windows notification area.

- Ink: `#102733`, the icon tile and repository banner.
- Mint: `#73e2c0`, the ceiling and product accent.
- Ivory: `#f5f4ed`, the H and display lettering.
- Slate: `#b4c6cc`, supporting copy on ink.

Use the full name **Headroom AgentOps**. Use the mark by itself for favicons and
tray icons, and the stacked wordmark in the dashboard. “Room to ship.” is the
repository signature. Preserve the mark's proportions and colors; avoid adding
shadows, effects or fine details that disappear at 16 pixels.

`agentops/brand/icon.svg` is the source of truth. The multi-resolution Windows
ICO contains 16, 20, 24, 32, 48, 64, 128 and 256 pixel images. PNG exports cover
browser and touch icons. `repository.svg` is the README banner.

Regenerate exports with `python scripts/build_brand.py` after installing Pillow
and defusedxml in an authoring environment. Neither is a runtime dependency.
All assets are local, packaged with the app, and require no remote image host.
