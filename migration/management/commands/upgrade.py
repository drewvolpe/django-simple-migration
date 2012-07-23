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

#
#
# Drew's superficial reviews of other upgrade tools:
#
# South - http://south.aeracode.org/wiki
# -set of 'migrations' that move db along
# -easy (and expected) to sometimes use sql directly
# -Good check-in activity
# -run and hosted by a college student (Andrew Godwin)
#
# Django Evolution - http://code.google.com/p/django-evolution/
# -Google summer of code '06 project
# -core django developer (Russell Keith-Magee) co-creator
# -good checkin activity
# -it tries to figure out 'evolutions' for you from the models
# -models are schema-of-record (very django-esque)
# -does not do data migration
# -immature; lots doesn't work
#
# dmigrations - http://code.google.com/p/dmigrations/
# -newcomer: created Sept 2008
# -lightweight: it generates sql which you can rework
# -creates set of 'migrations' which you can then run
#


def load_module(code_path):
    try:
        try:
            code_dir = os.path.dirname(code_path)
            code_file = os.path.basename(code_path) 

            fin = open(code_path, 'rb')

            return imp.load_source(hashlib.sha256(code_path).hexdigest(), code_path, fin)
        finally: 
            try: fin.close()
            except: pass
    except ImportError, x:
        traceback.print_exc(file = sys.stderr)
        raise
    except:
        traceback.print_exc(file = sys.stderr)
        raise

def run_migration(upgrade_filepath, quiet=True):
    """ takes in path to .sql or .py and runs it """
    if upgrade_filepath.endswith('.sql'):
        f = open(upgrade_filepath, 'r')
        file_str = f.read()

        # strip the utf8 bom
        if file_str.startswith(codecs.BOM_UTF8):
            file_str = file_str.lstrip(codecs.BOM_UTF8)

        # TODO mpk 10/11/11: I think there's two issues with this regex: 1) handling commented out
        # SQL statements (i.e., if there's a ; inside of a SQL comment, the upgrader chokes on it,
        # thinking it needs to execute an empty command; 2) handling $$-quoted strings (like PL/SQL
        # functions)
        reg = re.compile('\;\W*\n')
        sql_strs = reg.split(file_str)
        count = 0
        for sql_str in sql_strs:
            sql_str = sql_str.strip()
            if len(sql_str) < 1:
                continue

            if not quiet:
                print "---"
                print "Executing: " + sql_str
                cursor = connection.cursor()
                cursor.execute(sql_str)

    elif upgrade_filepath.endswith('.py'):
        m = load_module(upgrade_filepath)
        m.run_migration()

class Upgrader:
   
    def __init__(self, dir, new_version, new_comment, quiet=False):
        self.dir = dir
        self.new_version = new_version
        self.new_comment = new_comment
        if not self.new_comment:
            self.new_comment = ''
        self.upgrades_to_run = []
        self.quiet = quiet

        db = settings.DATABASES['default']

        self.latest_version = 0
        self.latest_date = None
        self.latest_comment = 'Initial version.'

        schema_migrations = SchemaMigration.objects.order_by('-new_version')
        print 'Found %s upgrades. ' % len(schema_migrations)
        if schema_migrations:
            self.latest_version = schema_migrations[0].new_version
            self.latest_date = schema_migrations[0].timestamp
            self.latest_comment = schema_migrations[0].comment

        if not self.new_version:
            return

        print "Latest version in db: " + str(self.latest_version)

        # find .sql or .py files which need to be run
        print "Looking for upgrades from version %s to version %s." %\
              (self.latest_version, self.new_version)
        for version_num in range(self.latest_version+1, self.new_version+1):
            sql_file = self.dir + '/' +  str(version_num) + '.sql'
            py_file = self.dir + '/' +  str(version_num) + '.py'
            if os.path.exists(sql_file) and os.path.exists(py_file):
                raise ValueError("Both sql and py file exist.  %s, %s" %\
                                 (sql_file, py_file))
            if os.path.exists(sql_file):
                self.upgrades_to_run.append((version_num, sql_file)) 
                continue
            if os.path.exists(py_file):
                self.upgrades_to_run.append((version_num, py_file)) 
                continue

    @transaction.commit_manually
    def execute(self):
        if len(self.upgrades_to_run) < 1:
            print "No upgrades to perform."
            return          

        try:
            for upgrade_tuple in self.upgrades_to_run:
                print "About to run file: " + upgrade_tuple[1]
                run_migration(upgrade_tuple[1], self.quiet)
                if not self.quiet:
                    print "Success."
                    print "---"

                # inc schema_version after each file
                self.__log_upgrade(str(upgrade_tuple[0]))
                    
        except Exception as e:
            print '!!!! Exception: %s' % str(e)
            print "Attempting rollback..."
            transaction.rollback()
            print "Rollback succeeded"
        else:
            transaction.commit()
            print "Successfully upgraded to version %s." % self.new_version
                
    def __log_upgrade(self, new_version):
        new_migration = SchemaMigration.objects.create(new_version=new_version,\
                                                       comment=self.new_comment)
        new_migration.save()
            
class Command(BaseCommand):
    
    option_list = BaseCommand.option_list + (
        make_option('-e', '--execute', dest='execute', action="store_true", \
                          help='Perform the upgrade (instead of displaying sql it would do).'),
        make_option('-d', '--directory', help='Directory where .sql files live.'),
        make_option('-n', '--new_version', type='int', dest='version', help='The data version to upgrade to.'),
        make_option('-c', '--comment', dest='comment', help='Upgrade comment.'),
        make_option('-q', '--quiet', action='store_true', dest='quiet',\
                    help='Quiet.  Suppress printing of sql/script.'),
    )

    def handle(self, *args, **options):
        
        print "Starting..."
        
        default_dir_path = './migration/db'
        if not options['directory']:
            if os.path.exists(default_dir_path):
                options['directory'] = default_dir_path
            else:
                print "No directory (-d) specified.  Run with -h for usage."
                sys.exit(1)
        if not os.path.exists(options['directory']):
            print "Directory %s does not exist." % options['directory']
            sys.exit(1)

        quiet = False
        if options['quiet']:
            quiet = True

        print "DJANGO SETTINGS: " + os.environ.get('DJANGO_SETTINGS_MODULE')
        upgrader = Upgrader(options['directory'], options['version'], options['comment'], quiet)
        print "Current version in the db: %s\nUpgraded on %s" %\
              (str(upgrader.latest_version), str(upgrader.latest_date)) 
        print "Last comment: %s" % upgrader.latest_comment

        if not options['version']:
            biggest_version = 0;
            for file in os.listdir(options['directory']):
                end_index = string.rfind(file, '.sql')
                if end_index == -1:
                    end_index = string.rfind(file, '.py')
                if end_index > -1:
                    try:
                        version = int(file[:end_index])
                        if version > biggest_version:
                            biggest_version = version
                    except:
                        i=43 # keep parser happy
            if upgrader and upgrader.latest_version and biggest_version <= upgrader.latest_version:
                print 'DB is up-to-date (version %s). No upgrade needed' % biggest_version
                sys.exit(0)
            print "No version (-n) specified."
            db_ver = upgrader.latest_version if upgrader.latest_version else -1
            print 'DB is on %s and %s has %s.' % (db_ver, options['directory'], biggest_version)
            print 'Need to run: -n %s -e' % biggest_version
            sys.exit(1)
        if not options['comment']:
            comment = ''

        print "Script(s) to-be-run:"
        for upgrade_tuple in upgrader.upgrades_to_run:
            print "---"
            print str(upgrade_tuple[0]) + " - " + upgrade_tuple[1]
            print "---"
            f = open(upgrade_tuple[1], 'r')
            file_str = f.read()

            # strip the utf8 bom
            if file_str.startswith(codecs.BOM_UTF8):
                file_str = file_str.lstrip(codecs.BOM_UTF8)

            if not quiet:
                print file_str
                
        if not options['execute']:
            print "Finished.  Run with -e to execute the upgrades."
            sys.exit(0)
        try:    
            upgrader.execute()
        except:
            traceback.print_exc(file = sys.stderr)
            sys.exit(1)
                
        print "done."
