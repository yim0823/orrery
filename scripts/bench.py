"""Does this hold up at the size of a real company?

The README claims blast radius is milliseconds on tens of thousands of entities. This
script is what that claim is based on, so it can be re-run and disagreed with.

    uv run python scripts/bench.py --hosts 10000

The generated world is synthetic and shaped like a mid-size estate: a few sites, racks
of hosts, a Kubernetes cluster per site, services spread across nodes, databases, and a
dependency web between services with a realistic fan-out.
"""
from __future__ import annotations

import argparse
import statistics
import time

from orrery.connectors.base import Discovery
from orrery.schema import (
    Entity,
    EntityKind,
    Relation,
    RelationKind,
    RelationStrength,
)
from orrery.sim import Event, propagate
from orrery.world import World, blast_radius, diff


def build(hosts: int, sites: int = 4, seed: int = 7) -> World:
    import random

    rng = random.Random(seed)
    d = Discovery()

    for s in range(sites):
        d.entities.append(Entity(id=f"site-{s}", kind=EntityKind.SITE, name=f"site {s}"))
        d.entities.append(
            Entity(
                id=f"cluster-{s}",
                kind=EntityKind.CLUSTER,
                name=f"cluster {s}",
                attrs={"quorum": 2},
            )
        )
        d.relations.append(
            Relation(src=f"cluster-{s}", dst=f"site-{s}", kind=RelationKind.HOSTED_IN)
        )

    node_ids: list[str] = []
    for i in range(hosts):
        s = i % sites
        h, n = f"host-{i}", f"node-{i}"
        d.entities.append(Entity(id=h, kind=EntityKind.HOST, name=h, attrs={"site": s}))
        d.entities.append(Entity(id=n, kind=EntityKind.NODE, name=n))
        d.relations.append(Relation(src=h, dst=f"site-{s}", kind=RelationKind.HOSTED_IN))
        d.relations.append(Relation(src=n, dst=h, kind=RelationKind.RUNS_ON))
        d.relations.append(Relation(src=n, dst=f"cluster-{s}", kind=RelationKind.MEMBER_OF))
        node_ids.append(n)

    services = max(1, hosts // 2)
    svc_ids: list[str] = []
    for i in range(services):
        sid = f"svc-{i}"
        replicas = rng.choice([1, 2, 2, 3, 3, 5])
        d.entities.append(
            Entity(id=sid, kind=EntityKind.SERVICE, name=sid, attrs={"replicas": replicas})
        )
        svc_ids.append(sid)
        for n in rng.sample(node_ids, min(replicas, len(node_ids))):
            d.relations.append(Relation(src=sid, dst=n, kind=RelationKind.RUNS_ON))

    databases = max(1, hosts // 20)
    for i in range(databases):
        did = f"db-{i}"
        d.entities.append(
            Entity(
                id=did,
                kind=EntityKind.DATABASE,
                name=did,
                attrs={"replica": rng.random() < 0.6},
            )
        )
        d.relations.append(
            Relation(src=did, dst=rng.choice(node_ids), kind=RelationKind.RUNS_ON)
        )
        for sid in rng.sample(svc_ids, min(6, len(svc_ids))):
            d.relations.append(Relation(src=sid, dst=did, kind=RelationKind.DEPENDS_ON))

    # service-to-service web, a fifth of it soft
    for sid in svc_ids:
        for dep in rng.sample(svc_ids, rng.choice([0, 1, 1, 2, 3])):
            if dep == sid:
                continue
            d.relations.append(
                Relation(
                    src=sid,
                    dst=dep,
                    kind=RelationKind.DEPENDS_ON,
                    strength=(
                        RelationStrength.SOFT if rng.random() < 0.2 else RelationStrength.HARD
                    ),
                )
            )

    w = World()
    w.ingest(d)
    return w


def _time(fn, repeat: int = 20) -> tuple[float, float]:
    samples = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    return statistics.median(samples), max(samples)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", type=int, default=10_000)
    ap.add_argument("--repeat", type=int, default=20)
    args = ap.parse_args()

    t = time.perf_counter()
    w = build(args.hosts)
    build_ms = (time.perf_counter() - t) * 1000

    print(f"world: {len(w):,} entities, {len(w.relations()):,} relations")
    print(f"  build+ingest   {build_ms:8.0f} ms")

    roots = ["site-0", f"host-{args.hosts // 2}", "db-0"]
    for root in roots:
        med, worst = _time(lambda r=root: blast_radius(w, r), args.repeat)
        reach = len(blast_radius(w, root).impacted)
        print(f"  blast {root:<16} {med:8.1f} ms  (worst {worst:6.1f})  reach {reach:,}")

    for root in roots:
        med, worst = _time(
            lambda r=root: propagate(w.fork(), Event(r, "down")), args.repeat
        )
        print(f"  simulate {root:<13} {med:8.1f} ms  (worst {worst:6.1f})  [includes fork]")

    med, _ = _time(lambda: w.fork(), args.repeat)
    print(f"  fork           {med:8.1f} ms")

    other = build(args.hosts, seed=8)
    med, _ = _time(lambda: diff(w, other), max(3, args.repeat // 4))
    print(f"  diff           {med:8.1f} ms")


if __name__ == "__main__":
    main()
