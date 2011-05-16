#!/usr/bin/env python
import codecs
import hashlib
import imp
import os
import os.path
import re
import string
import sys
import traceback

from optparse import OptionParser
from optparse import make_option

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import reset_queries, close_connection
from django.db import _rollback_on_exception, connection, transaction

from siphon.migration.models import *
from siphon.migration.management.commands.upgrade import run_migration

            
class Command(BaseCommand):
    
    option_list = BaseCommand.option_list + (
        make_option('-d', '--directory', help='Directory where .sql files live.',\
                        default='./migration/db'),
        make_option('-q', '--quiet', action='store_true', dest='quiet',\
                    help='Quiet.  Suppress printing of sql/script.', default=False),
    )

    @transaction.commit_manually
    def handle(self, *args, **options):
        print 'starting..'
        if not os.path.exists(options['directory']):
            print "Directory %s does not exist." % options['directory']
            sys.exit(1)
        
        dir_path = options['directory']
        latest_migration = None
        new_latest_migration = None
        try:
            schema_migrations = SchemaMigration.objects.order_by('-new_version')
            if not schema_migrations:
                print 'No migrations in db...'
                sys.exit(1)
            elif len(schema_migrations) < 2:
                print 'Error: only 1 migration in db.'
                sys.exit(1)
                # TODO - handle this case better

            latest_migration = schema_migrations[0]
            new_latest_migration = schema_migrations[1] # penultimate version
            print 'Looking for downgrader for %s' % latest_migration.new_version

            # find .sql or .py files which need to be run
            sql_file = os.path.join(dir_path, 'd_%s.sql' % str(latest_migration.new_version))
            py_file = os.path.join(dir_path, 'd_%s.py' % str(latest_migration.new_version))
            if os.path.exists(sql_file) and os.path.exists(py_file):
                raise ValueError("Both sql and py file exist.  %s, %s" %\
                                     (sql_file, py_file))
            if os.path.exists(sql_file):
                run_migration(sql_file, options['quiet'])
            elif os.path.exists(py_file):
                run_migration(py_file, options['quiet'])
            else:
                raise Exception("Couldn't find downgrade file to run ('%s' or '%s')" % \
                                (sql_file, py_file))

            print 'Deleting latest version in db: %s' % latest_migration.new_version
            latest_migration.delete()       

        except Exception as e:
            print '!!!!!! Exception: %s' % str(e)
            print "Attempting rollback..."
            transaction.rollback()
            print "Rollback succeeded"
        else:
            transaction.commit()
            print "Successfully downgraded to version %s." % new_latest_migration

        print 'done.'


