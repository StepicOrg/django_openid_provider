# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OpenID',
            fields=[
                ('id', models.AutoField(primary_key=True, verbose_name='ID', auto_created=True, serialize=False)),
                ('openid', models.CharField(max_length=200, blank=True, unique=True)),
                ('default', models.BooleanField(default=False)),
                ('user', models.ForeignKey(to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name_plural': 'OpenIDs',
                'ordering': ['openid'],
                'verbose_name': 'OpenID',
            },
        ),
        migrations.CreateModel(
            name='TrustedRoot',
            fields=[
                ('id', models.AutoField(primary_key=True, verbose_name='ID', auto_created=True, serialize=False)),
                ('trust_root', models.CharField(max_length=200)),
                ('openid', models.ForeignKey(to='openid_provider.OpenID')),
            ],
        ),
    ]
