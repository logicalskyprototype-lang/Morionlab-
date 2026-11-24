# MotionLab database migrations

Production startup must not create or mutate schema automatically. Set
`MOTIONLAB_ENV=production` and run:

```bash
alembic upgrade head
```

The first revision adopts an existing v0.2 schema created by SQLAlchemy
`create_all()`: it creates missing tables and indexes without dropping or
rewriting existing rows, then records the revision in `alembic_version`.

Take a PostgreSQL backup before migration and verify it on a staging copy.
This baseline migration intentionally has a no-op downgrade because dropping
these tables could destroy data that existed before Alembic adoption. Future
schema changes must use new forward migrations and a separately reviewed
restore/rollback plan.

For a fresh database, the same command creates the complete v0.2 schema.
