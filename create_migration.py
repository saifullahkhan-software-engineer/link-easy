import sys
import alembic.config

if len(sys.argv) < 2:
    print("Usage: python create_migration.py <migration_message>", file=sys.stderr)
    sys.exit(1)

migration_message = sys.argv[1]
alembic.config.main(argv=['revision', '--autogenerate', '-m', migration_message])
