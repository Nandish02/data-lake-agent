"""Build the mini data lake: mirror a handful of LakeQA dataset folders from the public S3 bucket.

The folders are copied *as-is* (catalog dumps, WARC headers, metadata and all), so the agent faces
the same messy layout as in the real million-file LakeQA lake, just with ~10 datasets instead of ~1M.
"""

import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BUCKET_URL = "https://lakeqa-yc4103-datalake.s3.amazonaws.com"
ROOT = Path(__file__).resolve().parent.parent
LAKE = ROOT / "lake"
TASKS = ROOT / "tasks"

# Datasets that are not needed by any demo task. They share vocabulary with the real targets
# (CTA ridership, NY lottery, Chicago) so the agent has to actually look before it picks a file.
DISTRACTORS = [
    "datagov/cta-ridership-annual-boarding-totals",
    "datagov/cta-ridership-daily-boarding-totals",
    "datagov/cta-ridership-l-station-entries-monthly-day-type-averages-totals",
    "datagov/lottery-aid-to-education-beginning-2002",
    "datagov/chicago-population-counts",
    "datagov/public-health-statistics-life-expectancy-by-community-area",
    "wikipedia/Chicago_Police_Department",
]

NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


def list_prefix(prefix: str) -> list[tuple[str, int]]:
    query = urllib.parse.urlencode({"list-type": "2", "prefix": prefix.rstrip("/") + "/"})
    with urllib.request.urlopen(f"{BUCKET_URL}/?{query}", timeout=60) as resp:
        tree = ET.fromstring(resp.read())
    return [
        (c.find("s3:Key", NS).text, int(c.find("s3:Size", NS).text))
        for c in tree.findall("s3:Contents", NS)
    ]


def download(key: str, size: int) -> bool:
    dest = LAKE / key
    if dest.exists() and dest.stat().st_size == size:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(f"{BUCKET_URL}/{urllib.parse.quote(key)}", timeout=300) as resp, open(tmp, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    if tmp.stat().st_size != size:
        raise RuntimeError(f"size mismatch for {key}: got {tmp.stat().st_size}, expected {size}")
    tmp.rename(dest)
    return True


def dataset_prefixes() -> list[str]:
    prefixes = set(DISTRACTORS)
    for task_file in sorted(TASKS.glob("*.json")):
        for key in json.loads(task_file.read_text())["datasets_used"]:
            source, name = key.split("/")[:2]
            prefixes.add(f"{source}/{name}")
    return sorted(prefixes)


def main() -> int:
    total_new = 0
    for prefix in dataset_prefixes():
        objects = list_prefix(prefix)
        if not objects:
            print(f"  !! nothing found under {prefix}", file=sys.stderr)
            return 1
        fetched = sum(download(key, size) for key, size in objects)
        total_new += fetched
        mb = sum(size for _, size in objects) / 1e6
        print(f"  {prefix:<80} {len(objects):>2} files {mb:6.1f} MB  ({fetched} downloaded)")
    print(f"lake ready at {LAKE} ({total_new} new files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
