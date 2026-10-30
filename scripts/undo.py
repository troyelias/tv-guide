"""Undo button for the playlist.

Usage: undo.py restore   put back the previous playlist in Dropbox and pause automatic updates
       undo.py resume    switch automatic updates back on (the next check-up uploads a fresh playlist)
"""
import hashlib, json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from dropbox_upload import upload, crypt  # noqa: E402

action = sys.argv[1]
state = json.loads(crypt(open("secure/state.enc", "rb").read(), decrypt=True)) if os.path.exists("secure/state.enc") else {}
if action == "restore":
    if not os.path.exists("secure/playlist_prev.enc"):
        sys.exit("::error::There is no earlier playlist saved yet.")
    prev = crypt(open("secure/playlist_prev.enc", "rb").read(), decrypt=True)
    upload(prev.decode("utf-8"))
    os.replace("secure/playlist_prev.enc", "secure/swap.tmp")
    if os.path.exists("secure/playlist_cur.enc"):
        os.replace("secure/playlist_cur.enc", "secure/playlist_prev.enc")
    os.replace("secure/swap.tmp", "secure/playlist_cur.enc")
    state["uploaded_hash"] = hashlib.sha256(prev).hexdigest()
    state["hold"] = True
    print("Previous playlist restored; automatic playlist updates paused.")
elif action == "resume":
    state["hold"] = False
    state["uploaded_hash"] = None
    print("Automatic playlist updates resumed.")
else:
    sys.exit("::error::Unknown action")
open("secure/state.enc", "wb").write(crypt(json.dumps(state).encode()))
