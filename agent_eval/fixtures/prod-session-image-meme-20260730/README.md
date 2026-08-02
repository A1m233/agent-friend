# Production image-meme incident fixture

This directory preserves the user-authorized, unredacted attachment from production session
`3f2685e3-b594-5b6e-8176-21e436904e1c`.

- Incident surface: `im:qq`
- Runtime model: `kimi/kimi-k2.7-code-highspeed`
- Attachment event: `3448cf5d-8ae1-5534-9f95-9f41fc400974`
- Attachment timestamp: `2026-07-30T08:15:42.380795Z`
- Display name: `meme-video-page.png`
- Media type: `image/png`
- Dimensions: `1290 x 2796`
- Bytes: `1015738`
- SHA-256 / asset id: `bc28ae227eb60463c74d8673159b4fe68c521893ee62e16aaaedf6c2b19e76ce`
- Bad assistant event: `05a7555d-4fa8-5d97-80d3-490fea9d244e`

The PNG is a byte-for-byte copy of the production attachment blob. Anchor cases retain the
causally relevant conversation from the initial meme question through the preceding no-result
`web_search` transaction. The full 821-event production session is not copied because earlier
topics are unrelated to this incident and would turn a focused fixture into a production-session
import.
