# MotionLab database migrations

Production startup must not create or mutate schema automatically. Set
`MOTIONLAB_ENV=production` and run:

```bash
alembic upgrade head
```

The first revision adopts an existing v0.2 schema created by SQLAlchemy
`create_all()`: it creates missing tables and indexes without dropping or
rewriting existing rows, then records the revision in `alembic_version`.

The second revision adds persistent API rate-limit buckets and durable audit
events. It safely adopts these tables if they already exist and does not
rewrite existing job or installation records.

Take a PostgreSQL backup before migration and verify it on a staging copy.
The baseline migration intentionally has a no-op downgrade because dropping
these tables could destroy data that existed before Alembic adoption. The
operational security migration also retains audit evidence on downgrade.
Future schema changes must use forward migrations and a separately reviewed
restore/rollback plan.
