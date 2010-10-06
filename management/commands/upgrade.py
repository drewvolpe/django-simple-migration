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

import psycopg2

from optparse import OptionParser
from optparse import make_option

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import reset_queries, close_connection, _rollback_on_exception

#
# Assumes a table exists with the following schema:
#
# CREATE TABLE "schema_version" (
#   "id" serial NOT NULL PRIMARY KEY,
#    "upgrade_timestamp" timestamp with time zone,
#    "new_version" varchar(20) NOT NULL,
#    "comment" varchar(200) NULL
# )
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

            return imp.load_source(hashlib.new(code_path).hexdigest(), code_path, fin)
        finally: 
            try: fin.close()
            except: pass
    except ImportError, x:
        traceback.print_exc(file = sys.stderr)
        raise
    except:
        traceback.print_exc(file = sys.stderr)
        raise

__VERSION_TABLE_NAME__ = "schema_version"

class Upgrader:
   
    def __init__(self, dir, new_version, new_comment, quiet=False):
        self.dir = dir
        self.new_version = new_version
        self.new_comment = new_comment
        self.upgrades_to_run = []
        self.quiet = quiet
        db = settings.DATABASES['default']
        self.connection = psycopg2.connect("dbname=%s user=%s" %\
                                           (db['NAME'], db['USER']))
        cursor = self.connection.cursor()

        has_version_table = False
        
        cursor.execute("SELECT tablename FROM pg_catalog.pg_tables")

        for row in cursor.fetchall():
            if cmp(row[0], __VERSION_TABLE_NAME__) == 0:
                has_version_table = True
                break;
        
        if not has_version_table:
            print "No version table found"
            raise Exception("No version table found.")

        self.latest_version = 0
        self.latest_date = None
        self.latest_comment = 'Initial version.'

        query = "SELECT new_version, upgrade_timestamp, comment FROM " +\
                __VERSION_TABLE_NAME__ + " ORDER BY new_version DESC"
        cursor.execute(query)
        row = cursor.fetchone()
        if row:
            (latest_version_str, self.latest_date, self.latest_comment) = row
            self.latest_version = int(latest_version_str)

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


    def execute(self):
        if len(self.upgrades_to_run) < 1:
            print "No upgrades to perform."
            return
            
        reg = re.compile('\;\W*\n')
        for upgrade_tuple in self.upgrades_to_run:
            print "About to run file: " + upgrade_tuple[1]
            if upgrade_tuple[1].endswith('.sql'):
                f = open(upgrade_tuple[1], 'r')
                file_str = f.read()

                # strip the utf8 bom
                if file_str.startswith(codecs.BOM_UTF8):
                    file_str = file_str.lstrip(codecs.BOM_UTF8)
                
                sql_strs = reg.split(file_str)
                count = 0
                for sql_str in sql_strs:
                    sql_str = sql_str.strip()
                    if len(sql_str) < 1:
                        continue
                    
                    if not self.quiet:
                        print "---"
                        print "Executing: " + sql_str
                    try:
                        cursor = self.connection.cursor()
                        cursor.execute(sql_str)
                    except:
                        print "EXCEPTION WHILE EXCUTING SQL:"
                        print sql_str
                        print "Attempting rollback..."
                        self.connection.rollback()
                        print "Rollback succeeded"
                        raise Exception("Exception while executing SQL.")

                try:
                    # inc schema_version and commit after each file
                    self.__log_upgrade(str(upgrade_tuple[0]))  
                    self.connection.commit()
                except:
                    print "EXCEPTION IN VERSION UPDATE/COMMIT"
                    print "Attempting rollback..."
                    self.connection.rollback()
                    print "Rollback succeeded"
                    raise

                if not self.quiet:
                    print "Success."
                    print "---"

            elif upgrade_tuple[1].endswith('.py'):
                m = load_module(upgrade_tuple[1])
                try:
                    try:
                        reset_queries()
                        m.run_upgrade()
                    except:
                        print "EXCEPTION IN VERSION UPDATE/COMMIT"
                        print "Attempting rollback..."
                        _rollback_on_exception()
                        print "Rollback succeeded"
                        raise
                finally:
                    close_connection()

                try:
                    self.__log_upgrade(str(upgrade_tuple[0]))
                    self.connection.commit()
                except:
                    print "EXCEPTION IN VERSION UPDATE/COMMIT"
                    print "Attempting rollback..."
                    self.connection.rollback()
                    print "Rollback succeeded"
                    raise
                 
        print "Successfully upgraded to version %s." % self.new_version
        
    def __log_upgrade(self, new_version):
        sql_str = "INSERT INTO %s (upgrade_timestamp, new_version, comment) " %\
                  __VERSION_TABLE_NAME__ +\
                  "VALUES ((SELECT NOW()), '%s', '%s')" % (new_version, self.new_comment)
        print 'sql: ' + str(sql_str)
        self.connection.cursor().execute(sql_str)
        
            
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
        
        if not options['directory']:
            if os.path.exists('./db'):
                options['directory'] = './db'
            else:
                print "No directory (-d) specified.  Run with -h for usage."
                sys.exit(1)
        if not os.path.exists(options['directory']):
            print "Directory %s does not exist." % options['directory']
            sys.exit(1)
        if not options['version']:
            print "No version (-n) specified. Run with -h for usage."
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
            print 'Biggest version of upgrades in %s seems to be: %s' %\
                  (options['directory'], str(biggest_version))               
            sys.exit(1)
        if not options['comment']:
            comment = ''
        quiet = False
        if options['quiet']:
            quiet = True
        
        print "SETTINGS: " + os.environ['DJANGO_SETTINGS_MODULE']
        
        upgrader = Upgrader(options['directory'], options['version'], options['comment'], quiet)
        
        print "Current app version %s, upgraded on %s" %\
              (str(upgrader.latest_version), str(upgrader.latest_date)) 
        print "Last comment: %s" % upgrader.latest_comment
        print "SQL to-be-run:"
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
                
        print "Finished."
        sys.exit(0)
