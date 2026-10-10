# Reading links people ask the bot about (blueprint Stage A).
#
#   urls.py     which URLs a message holds, canonical forms, what kind each is
#   safety.py   outbound HTTP that can't be pointed at private addresses (SSRF), with size/time caps
#   cache.py    short-lived results cache with in-flight dedupe
#   x_post.py   X/Twitter posts through the free fxtwitter API (text, author, quote, media)
#   webpage.py  articles and other HTML pages -> readable text + metadata
#   resolve.py  which links a Discord message refers to (LinkRef l1..l4), and what the model sees
#
# Videos stay with utils/media: an X post's text and images are read here, its video (if any)
# is watched by inspect_video. Reddit isn't read (Dean, 2026-10-09: rarely used).
