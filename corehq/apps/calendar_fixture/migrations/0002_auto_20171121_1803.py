# -*- coding: utf-8 -*-
# Generated by Django 1.11.7 on 2017-11-21 18:03
from __future__ import unicode_literals

from __future__ import absolute_import
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('calendar_fixture', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='calendarfixturesettings',
            name='days_after',
            field=models.PositiveIntegerField(default=90),
        ),
    ]