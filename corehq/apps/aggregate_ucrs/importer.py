from uuid import UUID

from corehq.apps.aggregate_ucrs.models import AggregateTableDefinition, PrimaryColumn, SecondaryTableDefinition, \
    SecondaryColumn


def import_aggregation_models_from_spec(spec):
    # todo: pretty sure this whole file can be completely replaced with DRF models...
    table_definition = _create_or_update_table_definition(spec)
    _update_primary_columns(spec, table_definition)
    _update_secondary_tables(spec, table_definition)
    return table_definition


def _create_or_update_table_definition(spec):
    try:
        table_definition = AggregateTableDefinition.objects.get(
            domain=spec.domain,
            table_id=spec.table_id,
        )
    except AggregateTableDefinition.DoesNotExist:
        table_definition = AggregateTableDefinition(
            domain=spec.domain,
            table_id=spec.table_id,
        )

    table_definition.display_name = spec.display_name
    table_definition.primary_data_source_id = UUID(spec.primary_table.data_source_id)
    table_definition.primary_data_source_key = spec.primary_table.key_column
    table_definition.aggregation_unit = spec.aggregation_config.unit
    table_definition.aggregation_start_column = spec.aggregation_config.start_column
    table_definition.aggregation_end_column = spec.aggregation_config.end_column
    table_definition.save()
    return table_definition

def _update_primary_columns(spec, table_definition):
    found_column_ids = set()
    for column in spec.primary_table.columns:
        try:
            db_column = PrimaryColumn.objects.get(table_definition=table_definition, column_id=column.column_id)
        except PrimaryColumn.DoesNotExist:
            db_column = PrimaryColumn(table_definition=table_definition, column_id=column.column_id)
        db_column.column_type = column.type
        db_column.config_params = column.config_params
        db_column.save()
        found_column_ids.add(db_column.pk)

    # delete any columns that were removed
    table_definition.primary_columns.exclude(pk__in=list(found_column_ids)).delete()


def _update_secondary_tables(spec, table_definition):
    for secondary_table_spec in spec.secondary_tables:
        try:
            db_secondary_table = SecondaryTableDefinition.objects.get(
                table_definition=table_definition,
                data_source=secondary_table_spec.data_source_id
            )
        except SecondaryTableDefinition.DoesNotExist:
            db_secondary_table = SecondaryTableDefinition(
                table_definition=table_definition,
                data_source=secondary_table_spec.data_source_id
            )
        db_secondary_table.data_source_key = secondary_table_spec.key_column
        db_secondary_table.aggregation_column = secondary_table_spec.aggregation_column
        db_secondary_table.save()
        _update_secondary_columns(secondary_table_spec, db_secondary_table)


def _update_secondary_columns(secondary_table_spec, db_secondary_table):
    for column in secondary_table_spec.columns:
        try:
            db_column = SecondaryColumn.objects.get(
                table_definition=db_secondary_table,
                column_id=column.column_id,
            )
        except SecondaryColumn.DoesNotExist:
            db_column = SecondaryColumn(
                table_definition=db_secondary_table,
                column_id=column.column_id,
            )
        db_column.aggregation_type = column.aggregation_type
        db_column.config_params = column.config_params
        db_column.save()
