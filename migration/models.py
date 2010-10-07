
from datetime import datetime

from django.db import models

class SchemaMigration(models.Model):
    timestamp = models.DateTimeField(blank=True, null=True, default=datetime.now)
    new_version = models.PositiveIntegerField()
    comment = models.TextField(blank=True)

    def __unicode__(self):
        return "Version: %s on: %s comment: %s" %\
               (self.new_version, str(self.timestamp), self.comment)
