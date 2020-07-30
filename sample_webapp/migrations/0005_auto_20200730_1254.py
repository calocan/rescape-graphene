# Generated by Django 2.0.7 on 2020-07-30 12:54

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sample_webapp', '0004_auto_20190109_1941'),
    ]

    operations = [
        migrations.CreateModel(
            name='Bar',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=20, unique=True)),
            ],
        ),
        migrations.AddField(
            model_name='foo',
            name='bars',
            field=models.ManyToManyField(to='sample_webapp.Bar'),
        ),
    ]
