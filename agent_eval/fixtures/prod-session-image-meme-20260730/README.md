# Production image-meme incident fixture

This directory preserves an image-evidence regression fixture. Production session and
event identifiers were removed for privacy; the image pixels remain unchanged.

- Incident surface: `im:qq`
- Runtime model: `kimi/kimi-k2.7-code-highspeed`
- Display name: `meme-video-page.png`
- Media type: `image/png`
- Dimensions: `1290 x 2796`
- Bytes: `1015738`
- SHA-256 / asset id: `bc28ae227eb60463c74d8673159b4fe68c521893ee62e16aaaedf6c2b19e76ce`

The PNG is a byte-for-byte copy of the production attachment blob. Anchor cases retain the
causally relevant conversation from the initial meme question through the preceding no-result
`web_search` transaction. The full 821-event production session is not copied because earlier
topics are unrelated to this incident and would turn a focused fixture into a production-session
import.
