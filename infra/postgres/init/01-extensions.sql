-- Extensions the platform depends on, created once when the local database is initialised.
--
-- PostGIS underpins merchant discovery, delivery-zone containment and courier proximity.
-- Migrations also create it defensively (`CREATE EXTENSION IF NOT EXISTS`), so a managed
-- database that provisions extensions separately needs no change here.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Trigram index support for merchant name search. Without it, the ILIKE '%term%' filter in
-- merchant search degrades to a sequential scan as the merchant table grows.

-- Logical schemas, one per bounded context. Migrations create these too; declaring them
-- here means a fresh local database is browsable before any migration has run.
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS identity;
CREATE SCHEMA IF NOT EXISTS merchant;
CREATE SCHEMA IF NOT EXISTS catalog;
