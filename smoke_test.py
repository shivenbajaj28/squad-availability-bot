"""Offline check of the storage layer and the tally message. No Telegram needed."""
import os, tempfile
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["BOT_TOKEN"] = "test"

from datetime import datetime, timedelta
import db, bot

db.init()
now = datetime.now(bot.TZ)
fid = db.create_fixture(-100123, "NUS vs NTU (Inter-Uni)", now + timedelta(days=4), now + timedelta(days=3), 1)

for uid, name in [(1,"Shiven"),(2,"Arjun"),(3,"Wei Ming"),(4,"Rahul"),(5,"Tom & Co")]:
    db.remember_user(uid, -100123, name, name.lower())

db.set_response(fid, 1, "Shiven", "yes")
db.set_response(fid, 2, "Arjun", "yes")
db.set_response(fid, 3, "Wei Ming", "maybe")
db.set_response(fid, 2, "Arjun", "no")          # changed mind, must overwrite not duplicate

f = db.get_fixture(fid)
print(bot.fixture_text(f))
print("---")
waiting = [u["name"] for u in db.non_responders(fid, -100123)]
assert waiting == ["Rahul", "Tom & Co"], waiting
assert len(db.responses_for(fid)) == 3
assert db.latest_fixture(-100123)["id"] == fid
assert len(db.pending_reminders()) == 1
db.mark_reminded(fid); assert len(db.pending_reminders()) == 0
print("reminder fires at:", bot.reminder_time(f))
for s in ["2026-09-19 14:00", "19/09/2026 14:00", "19-09-2026 14:00"]:
    assert bot.parse_when(s).hour == 14
print("ALL CHECKS PASSED")
