import os

from django.db.models.signals import post_syncdb

# TODO - set app name more cleanly; in settings variable ??

import siphon
from siphon.migration.models import *
 
def migration_syncdb(sender, **kwargs):

    schema_migrations = SchemaMigration.objects.order_by('-new_version')
    pwd = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    migrations_path = os.path.join(pwd, 'db')

    latest_version = 1
    while True:
        file_path = os.path.join(migrations_path, str(latest_version))
        if os.path.exists(file_path + '.sql') or \
           os.path.exists(file_path + '.py'):
            latest_version += 1
            continue
        break

    if schema_migrations:
        latest_migration = schema_migrations[0]
        if latest_migration.new_version < latest_version:
            print 'Last migration run %s is older than latest on disk %s; '  %\
                (latest_migration.new_version, latest_version) +\
                'you may need to migrate the schema: \'python manage.py upgrade\' '
                
            return
        print 'Schema appears up-to-date with migration files on disk'
        return

    # no migrations in DB, so assume latest migration file is schema version
    SchemaMigration.objects.create(new_version=latest_version,\
                                   comment = 'Set automatically during syncdb')
    print 'Schema migration set to latest version: %s' % latest_version

post_syncdb.connect(migration_syncdb, sender=siphon.migration.models)
