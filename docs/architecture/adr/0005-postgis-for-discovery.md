# ADR 0005: PostGIS for merchant discovery, with polygons over radii

- **Status**: Accepted
- **Date**: 2026-08-05

## Context

`GET /merchants?lat=&lng=` is the first request of every session and the busiest endpoint in the
platform. It has to answer three questions simultaneously:

1. Which merchants are near this point?
2. Which of them actually deliver to it?
3. Which of them are open right now?

Each has a naive implementation that does not survive contact with reality.

## Decision

All three are answered by one indexed PostGIS query.

### Proximity: `geography`, not `geometry`

```sql
WHERE ST_DWithin(m.location, :point, :radius_m)
ORDER BY ST_Distance(m.location, :point), m.id
```

`geography` returns metres directly, with no projection step. With `geometry` in SRID 4326,
distances come back in degrees, and a degree of longitude is 111 km at the equator and 101 km in
Abu Dhabi — so a "5 km" radius expressed in degrees is wrong by kilometres, differently in each
direction. The alternative is projecting to a local UTM zone on every query, which is a
per-market configuration nobody will maintain.

`ST_DWithin` is GiST-index-assisted, so the index filters before any distance is computed.

The `ORDER BY` includes `m.id` as a tiebreaker. Without a unique tiebreaker, two merchants at
identical distance can repeat or vanish across pages of the keyset pagination.

### Coverage: polygons take precedence over radii

```sql
AND (
     (EXISTS (active service areas) AND EXISTS (area covering :point))
  OR (NOT EXISTS (active service areas) AND ST_DWithin(m.location, :point, m.delivery_radius_m))
)
```

A radius is wrong in both directions for real cities. In Abu Dhabi, a merchant on the main island
and a customer on Al Reem may be two kilometres apart in a straight line and a long detour by
road; a merchant near Yas Island and a customer across the water are close by radius and
undeliverable in practice. A radius either excludes reachable customers or promises deliveries
that cannot be made, and the second is worse.

So a merchant with active service-area polygons is matched by containment
(`ST_Covers(boundary, point)`), and the radius is only the fallback for merchants that have not
drawn one. Both paths are tested, including the case where a generous radius must *not* override
a polygon.

### Open now: local wall-clock time, per merchant

Opening hours are stored as `TIME` values against a weekday, with the merchant's timezone on the
merchant row. The predicate covers three cases:

```sql
(closes_at > opens_at AND day_of_week = today     AND local_time >= opens_at AND local_time < closes_at)
(closes_at <= opens_at AND day_of_week = today     AND local_time >= opens_at)
(closes_at <= opens_at AND day_of_week = yesterday AND local_time < closes_at)
```

The third case is the one that gets missed. A merchant trading 18:00–02:00 must report open at
01:00 on Tuesday, and the row that matches is stored against **Monday**. An implementation that
only checks today's rows tells late-night customers everything is closed — during the second
busiest window of the day.

The comparison happens per merchant, in `timezone(m.timezone, now())`, not in one
platform-wide offset. A test asserts that two merchants with identical windows in `Asia/Dubai`
and `Europe/London` disagree about being open at the same instant.

## Why not the alternatives

**Bounding box in SQL, exact filtering in Python.** Works, and is what you do without PostGIS. It
means transferring every candidate row to the application, and it cannot express polygon
containment at all without reimplementing point-in-polygon. `bounding_box` exists in
`marsool_core.geo` for cheap in-process estimates in the dispatch scoring function, but not for
discovery.

**Redis GEO commands.** `GEOSEARCH` is fast and would serve proximity well. It cannot do polygon
containment, cannot join to opening hours, and would need a synchronisation mechanism keeping it
consistent with PostgreSQL. Reconsider as a read-through cache in front of this query if
discovery latency becomes the constraint — not as the source of truth.

**Elasticsearch.** Handles geo and text search together and would be the right answer once
discovery needs relevance ranking over text, cuisine, popularity and personalisation. Rejected
now as a second datastore to keep in sync for a query PostGIS already answers in single-digit
milliseconds.

**A materialised `is_open_now` column, refreshed by a cron job.** Rejected. It is wrong between
refreshes, and "wrong" here means either sending orders to a closed kitchen or hiding an open
one.

## Consequences

**Good**

- One query, one round trip, index-assisted.
- Distances in metres, so radii in configuration mean what they say.
- Coverage that matches reality, expressible per merchant.
- Correct behaviour at 01:00 and for multi-timezone expansion, tested.

**Bad, and accepted**

- PostGIS is a hard dependency: local development, CI and production all need the extension.
  Handled by the `postgis/postgis` image, an `apt` package in CI, and `CREATE EXTENSION IF NOT
  EXISTS` in migrations.
- The open-now predicate is a correlated `EXISTS` per candidate row. It is confined to
  `repository.py` with the three cases named in comments, and it is indexed by
  `(merchant_id, day_of_week)`. If it ever becomes the bottleneck, the fix is a materialised
  per-merchant open-interval table refreshed on `merchant.hours_updated` — correct by
  construction because it is event-driven rather than time-driven.
- Coordinates are stored twice: `NUMERIC` scalars for clients, a `geography` column for queries.
  The derived column has to be recomputed whenever a scalar changes. That is exactly the bug this
  repository shipped and fixed — relocating a merchant recomputed the point from stale scalars —
  and there is now a test asserting a relocated merchant leaves its old search radius.
- Straight-line distance is not road distance. Inflated by a documented factor
  (`ROAD_DISTANCE_FACTOR`) until a routing engine is integrated in Step 3, which is honest about
  being an estimate rather than pretending precision.
