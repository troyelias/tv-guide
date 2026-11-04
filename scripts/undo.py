"""Undo button for the playlist.

Usage: undo.py restore   put back the previous playlist in Dropbox and pause automatic updates
       undo.py resume    switch automatic updates back on (the next check-up uploads a fresh playlist)
"""
import hashlib, json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from dropbox_upload import upload, state_crypt as crypt, is_json, is_playlist  # noqa: E402

action = sys.argv[1]
state = json.loads(crypt(open("secure/state.enc", "rb").read(), decrypt=True, check=is_json)) if os.path.exists("secure/state.enc") else {}
if action == "restore":
    if not os.path.exists("secure/playlist_prev.enc"):
        sys.exit("::error::There is no earlier playlist saved yet.")
    prev = crypt(open("secure/playlist_prev.enc", "rb").read(), decrypt=True, check=is_playlist)
    digest = hashlib.sha256(prev).hexdigest()
    if state.get("hold") and state.get("uploaded_hash") == digest:
        # pressing undo twice must not bring the bad list back: the earlier list is already on the TV
        print("The earlier playlist is already on the TV and updates are paused - nothing to do.")
        sys.exit(0)
    upload(prev.decode("utf-8"))
    state["uploaded_hash"] = digest
    state["hold"] = True
    print("Previous playlist restored; automatic playlist updates paused.")
elif action == "resume":
    state["hold"] = False
    state["uploaded_hash"] = None
    print("Automatic playlist updates resumed.")
else:
    sys.exit("::error::Unknown action")
open("secure/state.enc", "wb").write(crypt(json.dumps(state).encode()))
