# Phase 3 baselines (dev split)

Dev split; generated 2026-11-16; policy model `claude-sonnet-5-5` (compaction_recall, object_level), judge model `claude-opus-5-5` (needs lists and checks). The stale-content rate is a lower bound: the superseded lists are known to be incomplete (PLAN.md 7 Phase 2, amendment 2).

| policy | setting | pruning rate | strict no-impact | no-impact (recall) | stale content | dropped current state | cut points | run date |
|---|---|---|---|---|---|---|---|---|
| none | - | 0% | 100% | 100% | 0% | 0% | 12 | 2026-11-16 |
| recency | n_turns=2 | 88% | 0% | 0% | 0% | 100% | 12 | 2026-11-16 |
| recency | n_turns=5 | 77% | 0% | 0% | 0% | 100% | 12 | 2026-11-16 |
| recency | n_turns=10 | 59% | 8% | 8% | 0% | 100% | 12 | 2026-11-16 |
| recency | n_turns=20 | 23% | 67% | 67% | 0% | 67% | 12 | 2026-11-16 |
| recency | n_turns=40 | 2% | 92% | 92% | 0% | 8% | 12 | 2026-11-16 |
| compaction_recall | target=0.4 | 43% | 25% | 58% | 0% | 33% | 12 | 2026-11-16 |
| compaction_recall | target=0.7 | 72% | 8% | 42% | 0% | 92% | 12 | 2026-11-16 |
| object_level | target=0.4 | 63% | 0% | 8% | 0% | 100% | 12 | 2026-11-16 |
| object_level | target=0.7 | 78% | 0% | 8% | 25% | 92% | 12 | 2026-11-16 |

## Recency curve

| N turns kept | pruning rate | strict no-impact | no-impact (recall) | stale content | dropped current state |
|---|---|---|---|---|---|
| 2 | 88% | 0% | 0% | 0% | 100% |
| 5 | 77% | 0% | 0% | 0% | 100% |
| 10 | 59% | 8% | 8% | 0% | 100% |
| 20 | 23% | 67% | 67% | 0% | 67% |
| 40 | 2% | 92% | 92% | 0% | 8% |

## Losses

Lost needs as item refs per cut id; lost current-state items as a count. IDs only.

### none (-)

- no losses

### recency (n_turns=2)

- s002.c1: need:n3, need:n4, need:n5, need:n7; current-state items lost: 7
- s002.c2: need:n4, need:n5, need:n6, need:n7, need:n8, need:n9; current-state items lost: 8
- s003.c1: need:n1, need:n4, need:n5, need:n6; current-state items lost: 7
- s003.c2: need:n1, need:n2, need:n4; current-state items lost: 8
- s011.c1: need:n1, need:n2, need:n3, need:n4, need:n7, need:n9; current-state items lost: 7
- s011.c2: need:n1, need:n2, need:n4, need:n5, need:n6, need:n8, need:n9, need:n10, need:n11, need:n12; current-state items lost: 10
- s013.c1: need:n1, need:n2, need:n4, need:n5, need:n6; current-state items lost: 9
- s013.c2: need:n4, need:n6, need:n7; current-state items lost: 11
- s015.c1: need:n1, need:n2, need:n3, need:n4, need:n5, need:n6, need:n7, need:n8, need:n10; current-state items lost: 13
- s015.c2: need:n3, need:n4, need:n5, need:n6, need:n7; current-state items lost: 10
- s017.c1: need:n1, need:n2, need:n5, need:n6, need:n7, need:n8, need:n9; current-state items lost: 8
- s017.c2: need:n1, need:n3, need:n4, need:n5, need:n6; current-state items lost: 13

### recency (n_turns=5)

- s002.c1: need:n3, need:n4, need:n5, need:n7; current-state items lost: 5
- s002.c2: need:n4, need:n5, need:n7, need:n8, need:n9; current-state items lost: 7
- s003.c1: need:n4, need:n5, need:n6; current-state items lost: 5
- s003.c2: need:n1, need:n4; current-state items lost: 8
- s011.c1: need:n3, need:n4, need:n7, need:n9; current-state items lost: 5
- s011.c2: need:n1, need:n4, need:n5, need:n6, need:n8, need:n9, need:n10, need:n11, need:n12; current-state items lost: 10
- s013.c1: need:n1, need:n2, need:n4, need:n5, need:n6; current-state items lost: 8
- s013.c2: need:n4, need:n6, need:n7; current-state items lost: 8
- s015.c1: need:n1, need:n2, need:n3, need:n4, need:n5, need:n6, need:n8, need:n10; current-state items lost: 11
- s015.c2: need:n3, need:n4, need:n5, need:n6, need:n7; current-state items lost: 8
- s017.c1: need:n6, need:n8, need:n9; current-state items lost: 7
- s017.c2: need:n1, need:n3, need:n4, need:n5, need:n6; current-state items lost: 12

### recency (n_turns=10)

- s002.c1: need:n3, need:n7; current-state items lost: 4
- s002.c2: need:n4, need:n5, need:n8; current-state items lost: 5
- s003.c1: need:n5; current-state items lost: 2
- s003.c2: no need lost; current-state items lost: 5
- s011.c1: need:n7, need:n9; current-state items lost: 2
- s011.c2: need:n1, need:n4, need:n5, need:n6, need:n8, need:n10, need:n12; current-state items lost: 7
- s013.c1: need:n1, need:n2, need:n4; current-state items lost: 4
- s013.c2: need:n4, need:n6, need:n7; current-state items lost: 7
- s015.c1: need:n1, need:n2, need:n3, need:n6, need:n8, need:n10; current-state items lost: 8
- s015.c2: need:n3, need:n4, need:n5, need:n6, need:n7; current-state items lost: 8
- s017.c1: need:n6, need:n9; current-state items lost: 5
- s017.c2: need:n1, need:n3, need:n4, need:n6; current-state items lost: 11

### recency (n_turns=20)

- s002.c1: no need lost; current-state items lost: 1
- s002.c2: no need lost; current-state items lost: 4
- s003.c2: no need lost; current-state items lost: 2
- s011.c2: need:n5, need:n8; current-state items lost: 3
- s015.c1: no need lost; current-state items lost: 1
- s015.c2: need:n3, need:n4, need:n5, need:n6, need:n7; current-state items lost: 4
- s017.c1: need:n9; current-state items lost: 2
- s017.c2: need:n3, need:n4; current-state items lost: 7

### recency (n_turns=40)

- s017.c2: need:n4; current-state items lost: 3

### compaction_recall (target=0.4)

- s002.c1: need:n7
- s011.c1: need:n9
- s013.c2: need:n7; current-state items lost: 1
- s015.c2: need:n7
- s017.c1: need:n6

### compaction_recall (target=0.7)

- s002.c1: need:n7
- s002.c2: need:n5
- s003.c1: need:n6
- s013.c2: need:n7; current-state items lost: 1
- s015.c1: need:n10
- s015.c2: need:n3, need:n7
- s017.c2: need:n1, need:n4; current-state items lost: 1

### object_level (target=0.4)

- s002.c1: need:n7
- s002.c2: need:n9; current-state items lost: 1
- s003.c1: need:n1, need:n5; current-state items lost: 4
- s003.c2: need:n4, need:n5
- s011.c1: need:n8, need:n9
- s011.c2: need:n8
- s013.c1: no need lost; current-state items lost: 1
- s013.c2: need:n4; current-state items lost: 5
- s015.c1: need:n5, need:n7; current-state items lost: 2
- s015.c2: need:n4, need:n7; current-state items lost: 2
- s017.c1: need:n1, need:n2, need:n3, need:n5, need:n6, need:n7, need:n8; current-state items lost: 5
- s017.c2: need:n3, need:n4; current-state items lost: 2

### object_level (target=0.7)

- s002.c1: need:n3; current-state items lost: 1
- s002.c2: need:n4, need:n5, need:n7, need:n8, need:n9; current-state items lost: 3
- s003.c1: need:n2, need:n6; current-state items lost: 1
- s003.c2: need:n4, need:n5; current-state items lost: 1
- s011.c1: need:n8, need:n9
- s011.c2: need:n8
- s013.c1: need:n5, need:n6; current-state items lost: 5
- s013.c2: need:n3, need:n4; current-state items lost: 7
- s015.c2: need:n4, need:n7; current-state items lost: 2
- s017.c1: need:n3, need:n6, need:n7; current-state items lost: 2
- s017.c2: need:n3, need:n4, need:n5, need:n8; current-state items lost: 1
