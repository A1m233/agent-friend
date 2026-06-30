# 033 - Ark models have inconsistent perceived visual size

- **Status**: open
- **Severity**: low
- **Discovered**: 2026-06-30
- **Related requirement**: 040-desktop-avatar-model-switching

## Phenomenon

During requirement 040 desktop smoke testing, Ark model `builtin:spine:ark-models:002_amiya` looked noticeably larger than some other Ark desktop pet models after switching models.

CDP sampling showed that the current fit path already normalizes most Ark models to roughly the same visible height, about 92-96 CSS px, but their visible width and filled area vary a lot:

- `002_amiya`: visible bounds about `70.4 x 96`, area about `6758`.
- Slimmer models include `102_texas` at about `46.4 x 94.4`, `103_angel` at about `55.2 x 96`, and `010_chen` at about `60.8 x 95.2`.
- Wider models include `263_skadi` at about `88.8 x 96`, `2025_shu` at about `81.6 x 92`, and `2027_wang` at about `80 x 92.8`.

So Amiya is not the largest measured Ark model, but compared with thinner silhouettes she can feel visually larger.

## Initial Root Cause

The current Spine desktop pet fit normalizes the rendered model by a layout / visible height target. It does not normalize perceived body mass, body width, head/body proportions, or decorative silhouette fullness.

Because Ark models have different costumes, props, hair shapes, capes, and body proportions, height-only normalization can still leave visible size differences.

## Impact And Judgment

This is a visual consistency issue, not a functional blocker for requirement 040:

- model switching, persistence, installed asset loading, and pet window runtime switching still work;
- ActionBar anchoring and switch-position preservation are separate behaviors and have been handled in 040;
- applying a global scale correction would likely make other Ark models too small or too large.

Defer this until there is a dedicated visual tuning pass for avatar model display metadata.

## Possible Fix Direction

1. Add optional per-model display metadata such as `visualScale`, `visualOffsetX`, and `visualOffsetY` to the avatar catalog / installed manifest.
2. Prefer model-authored anchors or sockets when available, for example body, head, or feet anchors, instead of deriving every UI/display decision from full rendered bounds.
3. Measure a representative set of Live2D and Ark models in the real pet window before setting default scale targets.
4. Avoid a single global Ark scale multiplier; use per-model overrides only for outliers.
