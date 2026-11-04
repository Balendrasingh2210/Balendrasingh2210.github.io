"""Fetch Balendra's coding stats and write stats.json + update the numbers in index.html.

Runs daily from .github/workflows/update-stats.yml. Every platform is fetched
independently; if one fails, its last known value from stats.json is kept, so a
temporary outage never breaks the site or zeroes a number.
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATS = ROOT / "stats.json"
INDEX = ROOT / "index.html"

HANDLES = {
    "leetcode": "balendrasingh310813",
    "codeforces": "Harry22",
    "codechef": "harry_2210",
}
# GeeksforGeeks hides solved counts behind a sign-in, so it (with HackerRank and
# CodeStudio) is part of the fixed offset below rather than fetched live.
# Total shown on Codolio when this was set up (4 Nov 2026). Used once to compute an
# offset for problems on platforms without a public API (GFG, HackerRank, CodeStudio)
# and for differences in how platforms count, so the total stays consistent.
SEED_TOTAL = 734

UA = "Mozilla/5.0 (portfolio stats updater; +https://balendrasingh2210.github.io/)"


def get(url, data=None, headers=None):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def leetcode():
    q = """query($u: String!) {
      matchedUser(username: $u) { submitStatsGlobal { acSubmissionNum { difficulty count } } }
      userContestRanking(username: $u) { rating attendedContestsCount }
      userContestRankingHistory(username: $u) { attended rating }
    }"""
    body = json.dumps({"query": q, "variables": {"u": HANDLES["leetcode"]}}).encode()
    d = json.loads(get("https://leetcode.com/graphql", body, {
        "Content-Type": "application/json", "Referer": "https://leetcode.com/"}))["data"]
    solved = next(x["count"] for x in d["matchedUser"]["submitStatsGlobal"]["acSubmissionNum"] if x["difficulty"] == "All")
    rk = d["userContestRanking"] or {}
    hist = [h["rating"] for h in (d.get("userContestRankingHistory") or []) if h.get("attended")]
    return {
        "solved": solved,
        "rating": round(rk.get("rating") or 0),
        "max_rating": round(max(hist)) if hist else round(rk.get("rating") or 0),
        "contests": rk.get("attendedContestsCount") or 0,
    }


def codeforces():
    h = HANDLES["codeforces"]
    info = json.loads(get(f"https://codeforces.com/api/user.info?handles={h}"))["result"][0]
    subs = json.loads(get(f"https://codeforces.com/api/user.status?handle={h}"))["result"]
    solved = {(s["problem"].get("contestId"), s["problem"].get("index")) for s in subs if s.get("verdict") == "OK"}
    contests = json.loads(get(f"https://codeforces.com/api/user.rating?handle={h}"))["result"]
    return {"solved": len(solved), "rating": info.get("rating", 0), "max_rating": info.get("maxRating", 0), "contests": len(contests)}


def codechef():
    html = get(f"https://www.codechef.com/users/{HANDLES['codechef']}")
    solved = re.search(r"Total Problems Solved:\s*(\d+)", html)
    rating = re.search(r'class="rating-number"[^>]*>\s*(\d+)', html)
    contests = re.search(r"No\. of Contests Participated:\s*<b>\s*(\d+)", html)
    if not solved:
        raise ValueError("CodeChef solved count not found")
    return {"solved": int(solved.group(1)),
            "rating": int(rating.group(1)) if rating else None,
            "contests": int(contests.group(1)) if contests else None}


def main():
    old = json.loads(STATS.read_text()) if STATS.exists() else {}
    platforms = old.get("platforms", {})
    ok = {}
    for name, fn in (("leetcode", leetcode), ("codeforces", codeforces), ("codechef", codechef)):
        try:
            fresh = fn()
            prev = platforms.get(name, {})
            # keep previous values for any field a parser could not read this time
            platforms[name] = {k: (v if v is not None else prev.get(k)) for k, v in fresh.items()}
            ok[name] = True
            print(f"{name}: {platforms[name]}")
        except Exception as e:  # keep last known values
            ok[name] = False
            print(f"{name}: FAILED ({e}); keeping {platforms.get(name)}", file=sys.stderr)

    live_sum = sum((platforms.get(p) or {}).get("solved") or 0 for p in HANDLES)
    platforms.pop("gfg", None)
    offset = old.get("offset")
    if offset is None and all(ok.values()):
        offset = SEED_TOTAL - live_sum
        print(f"Seeded offset = {offset}")
    total = live_sum + offset if offset is not None else old.get("total_solved", SEED_TOTAL)

    lc = platforms.get("leetcode") or {}
    contests = sum((platforms.get(p) or {}).get("contests") or 0 for p in ("leetcode", "codeforces", "codechef"))
    now = datetime.now(timezone.utc)
    stats = {
        "total_solved": total,
        "leetcode_rating": lc.get("rating"),
        "leetcode_max_rating": lc.get("max_rating"),
        "contests": contests or old.get("contests"),
        "updated": now.strftime("%Y-%m-%d"),
        "updated_label": now.strftime("%-d %B %Y"),
        "offset": offset,
        "platforms": platforms,
    }
    STATS.write_text(json.dumps(stats, indent=2) + "\n")

    # Keep the static HTML in step, so search engines and no-JS visitors see current numbers.
    html = INDEX.read_text()
    def put(key, value):
        nonlocal html
        if value is None:
            return
        html = re.sub(r'(data-stat="%s"[^>]*>)[^<]*(<)' % key, r"\g<1>%s\g<2>" % value, html)
    put("total_solved", f"{stats['total_solved']}")
    put("leetcode_rating", stats["leetcode_rating"])
    put("leetcode_max_rating", stats["leetcode_max_rating"])
    put("contests", stats["contests"])
    put("updated_label", stats["updated_label"])
    html = re.sub(r'(data-count=")[^"]*("[^>]*data-stat="total_solved")', r"\g<1>%s\g<2>" % stats["total_solved"], html)
    html = re.sub(r'(data-count=")[^"]*("[^>]*data-stat="leetcode_rating")', r"\g<1>%s\g<2>" % stats["leetcode_rating"], html)
    INDEX.write_text(html)


if __name__ == "__main__":
    main()
