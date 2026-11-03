"""Replace the playlist's Dropbox share link: switch off every existing link to the file and make a new one.
Use it if the link may have been passed around. Prints no link - copy the new one from the Dropbox app
(Share -> Copy link, then change dl=0 to dl=1) and put it in the TV app."""
import json, os, sys, urllib.error
sys.path.insert(0, os.path.dirname(__file__))
from dropbox_upload import api  # noqa: E402

path = os.environ.get("DBX_PATH", "").strip()


def explain(e):
    body = e.read().decode("utf-8", "ignore") if hasattr(e, "read") else str(e)
    if "missing_scope" in body:
        sys.exit("::error::The Dropbox app isn't allowed to manage share links. In the Dropbox App Console -> your app -> "
                 "Permissions, tick sharing.read and sharing.write, press Submit, then run 'Dropbox setup (one time)' again.")
    sys.exit(f"::error::Dropbox refused ({getattr(e, 'code', '?')}).")


try:
    links = api("sharing/list_shared_links", {"path": path, "direct_only": True}).get("links", [])
    for l in links:
        api("sharing/revoke_shared_link", {"url": l["url"]})
    api("sharing/create_shared_link_with_settings", {"path": path})
except urllib.error.HTTPError as e:
    explain(e)
print(f"Switched off {len(links)} old link(s) and made a new one. Copy it from the Dropbox app (Share -> Copy link), "
      "change dl=0 to dl=1, and update it in Chillio / IPTV Deck. The old link no longer works.")
