"""Command line inside the container:
  python -m app.cli index [collection] [--force]
  python -m app.cli search "<query>" [collection] [--top 5]
  python -m app.cli ask "<question>" [collection]
  python -m app.cli status
"""
import json
import sys

from . import config
from .store import Store


def main(a):
    s = config.load()
    st = Store(s)
    force = "--force" in a
    a = [x for x in a if x != "--force"]
    top = 5
    if "--top" in a:
        i = a.index("--top"); top = int(a[i + 1]); a = a[:i] + a[i + 2:]
    cmd = a[0] if a else "status"
    if cmd == "index":
        res = st.index(a[1], force) if len(a) > 1 else st.index_all(force)
        print(json.dumps(res, indent=1))
    elif cmd == "search":
        cols = a[2].split(",") if len(a) > 2 else list(s.collections)
        r = st.search(a[1], cols, top)
        print("mode:", r["mode"])
        for x in r["results"]:
            print(f"--- {x['score']} [{x['collection']}] {x['title']} | {x['section']} | {x['page'] or x['source']}")
            print("   ", x["text"][:300].replace("\n", " "))
    elif cmd == "ask":
        cols = a[2].split(",") if len(a) > 2 else list(s.collections)
        print(json.dumps(st.ask(a[1], cols), indent=1, ensure_ascii=False))
    else:
        print(json.dumps(st.info(list(s.collections)), indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
