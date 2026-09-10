-- stg mirrors raw landed data; ddh is the modelled layer the semantic
-- layer reads from; app holds boards and cards.
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS ddh;
CREATE SCHEMA IF NOT EXISTS app;

-- dm_planning is the second business mart, and it exists so that
-- per-dashboard schema scoping is a thing that can be demonstrated rather
-- than only described. Planning is genuinely a different subject from
-- operations: a target is a decision somebody made, not a reading somebody
-- took, and nobody answers a question about one by reading the other.
CREATE SCHEMA IF NOT EXISTS dm_planning;
