# encoding: utf-8
import datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models

class Migration(SchemaMigration):

    def forwards(self, orm):
        
        # Removing unique constraint on 'CaseDomainMapping', fields ['case_id', 'domain_id']
        db.delete_unique(u'stock_casedomainmapping', ['case_id', 'domain_id'])

        # Deleting field 'CaseDomainMapping.domain_id'
        db.delete_column(u'stock_casedomainmapping', 'domain_id')

        # Adding field 'CaseDomainMapping.domain_name'
        db.add_column(u'stock_casedomainmapping', 'domain_name', self.gf('django.db.models.fields.CharField')(default='', max_length=100), keep_default=False)

        # Adding unique constraint on 'CaseDomainMapping', fields ['case_id', 'domain_name']
        db.create_unique(u'stock_casedomainmapping', ['case_id', 'domain_name'])


    def backwards(self, orm):
        
        # Removing unique constraint on 'CaseDomainMapping', fields ['case_id', 'domain_name']
        db.delete_unique(u'stock_casedomainmapping', ['case_id', 'domain_name'])

        # User chose to not deal with backwards NULL issues for 'CaseDomainMapping.domain_id'
        raise RuntimeError("Cannot reverse this migration. 'CaseDomainMapping.domain_id' and its values cannot be restored.")

        # Deleting field 'CaseDomainMapping.domain_name'
        db.delete_column(u'stock_casedomainmapping', 'domain_name')

        # Adding unique constraint on 'CaseDomainMapping', fields ['case_id', 'domain_id']
        db.create_unique(u'stock_casedomainmapping', ['case_id', 'domain_id'])


    models = {
        u'stock.casedomainmapping': {
            'Meta': {'unique_together': "(('case_id', 'domain_name'),)", 'object_name': 'CaseDomainMapping'},
            'case_id': ('django.db.models.fields.CharField', [], {'max_length': '100', 'db_index': 'True'}),
            'domain_name': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'})
        },
        u'stock.stockreport': {
            'Meta': {'object_name': 'StockReport'},
            'date': ('django.db.models.fields.DateTimeField', [], {'db_index': 'True'}),
            'form_id': ('django.db.models.fields.CharField', [], {'max_length': '100', 'db_index': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'type': ('django.db.models.fields.CharField', [], {'max_length': '20'})
        },
        u'stock.stockstate': {
            'Meta': {'unique_together': "(('section_id', 'case_id', 'product_id'),)", 'object_name': 'StockState'},
            'case_id': ('django.db.models.fields.CharField', [], {'max_length': '100', 'db_index': 'True'}),
            'daily_consumption': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '5'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'last_modified_date': ('django.db.models.fields.DateTimeField', [], {}),
            'product_id': ('django.db.models.fields.CharField', [], {'max_length': '100', 'db_index': 'True'}),
            'section_id': ('django.db.models.fields.CharField', [], {'max_length': '100', 'db_index': 'True'}),
            'stock_on_hand': ('django.db.models.fields.DecimalField', [], {'default': "'0'", 'max_digits': '20', 'decimal_places': '5'})
        },
        u'stock.stocktransaction': {
            'Meta': {'object_name': 'StockTransaction'},
            'case_id': ('django.db.models.fields.CharField', [], {'max_length': '100', 'db_index': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'product_id': ('django.db.models.fields.CharField', [], {'max_length': '100', 'db_index': 'True'}),
            'quantity': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '20', 'decimal_places': '5'}),
            'report': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['stock.StockReport']"}),
            'section_id': ('django.db.models.fields.CharField', [], {'max_length': '100', 'db_index': 'True'}),
            'stock_on_hand': ('django.db.models.fields.DecimalField', [], {'max_digits': '20', 'decimal_places': '5'}),
            'subtype': ('django.db.models.fields.CharField', [], {'max_length': '20', 'null': 'True', 'blank': 'True'}),
            'type': ('django.db.models.fields.CharField', [], {'max_length': '20'})
        }
    }

    complete_apps = ['stock']
