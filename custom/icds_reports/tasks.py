from __future__ import absolute_import

from __future__ import unicode_literals
from collections import namedtuple
import csv342 as csv
from datetime import date, datetime, timedelta
import io
import logging
import os

from celery import chain, group
from celery.schedules import crontab
from celery.task import periodic_task, task
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import Error, IntegrityError, connections, transaction
from django.db.models import F

from corehq.apps.locations.models import SQLLocation
from corehq.apps.userreports.models import get_datasource_config
from corehq.apps.userreports.util import get_indicator_adapter, get_table_name
from corehq.const import SERVER_DATE_FORMAT
from corehq.form_processor.change_publishers import publish_case_saved
from corehq.form_processor.interfaces.dbaccessors import CaseAccessors
from corehq.sql_db.connections import get_icds_ucr_db_alias
from corehq.util.decorators import serial_task
from corehq.util.log import send_HTML_email
from corehq.util.soft_assert import soft_assert
from corehq.util.view_utils import reverse
from custom.icds_reports.const import DASHBOARD_DOMAIN
from custom.icds_reports.models import (
    AggChildHealthMonthly,
    AggregateComplementaryFeedingForms,
    AggregateGrowthMonitoringForms,
    AggregateChildHealthPostnatalCareForms,
    AggregateChildHealthTHRForms,
    UcrTableNameMapping)
from custom.icds_reports.reports.issnip_monthly_register import ISSNIPMonthlyReport
from custom.icds_reports.utils import zip_folder, create_pdf_file, icds_pre_release_features, track_time
from dimagi.utils.chunked import chunked
from dimagi.utils.dates import force_to_date
from dimagi.utils.logging import notify_exception
import six
from six.moves import range
from io import open

celery_task_logger = logging.getLogger('celery.task')

UCRAggregationTask = namedtuple("UCRAggregationTask", ['type', 'date'])

DASHBOARD_TEAM_MEMBERS = ['jemord', 'ssrikrishnan', 'mharrison', 'vmaheshwari', 'stewari']
DASHBOARD_TEAM_EMAILS = ['{}@{}'.format(member_id, 'dimagi.com') for member_id in DASHBOARD_TEAM_MEMBERS]
_dashboard_team_soft_assert = soft_assert(to=DASHBOARD_TEAM_EMAILS)


UCR_TABLE_NAME_MAPPING = [
    {'type': "awc_location", 'name': 'static-awc_location'},
    {'type': 'awc_mgmt', 'name': 'static-awc_mgt_forms'},
    {'type': 'ccs_record_monthly', 'name': 'static-ccs_record_cases_monthly_tableau_v2'},
    {'type': 'child_health_monthly', 'name': 'static-child_cases_monthly_tableau_v2'},
    {'type': 'daily_feeding', 'name': 'static-daily_feeding_forms'},
    {'type': 'household', 'name': 'static-household_cases'},
    {'type': 'infrastructure', 'name': 'static-infrastructure_form'},
    {'type': 'person', 'name': 'static-person_cases_v2'},
    {'type': 'usage', 'name': 'static-usage_forms'},
    {'type': 'vhnd', 'name': 'static-vhnd_form'},
    {'type': 'complementary_feeding', 'is_ucr': False, 'name': 'icds_dashboard_comp_feed_form'},
    {'type': 'aww_user', 'name': 'static-commcare_user_cases'},
    {'type': 'child_tasks', 'name': 'static-child_tasks_cases'},
    {'type': 'pregnant_tasks', 'name': 'static-pregnant-tasks_cases'},
    {'type': 'thr_form', 'is_ucr': False, 'name': 'icds_dashboard_child_health_thr_forms'},
    {'type': 'child_list', 'name': 'static-child_health_cases'},
]

SQL_FUNCTION_PATHS = [
    ('migrations', 'sql_templates', 'database_functions', 'update_months_table.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'update_location_table.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'create_new_table_for_month.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'create_new_agg_table_for_month.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'insert_into_child_health_monthly.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'insert_into_ccs_record_monthly.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'insert_into_daily_attendance.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'aggregate_child_health.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'aggregate_ccs_record.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'aggregate_awc_data.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'aggregate_location_table.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'aggregate_awc_daily.sql'),
    ('migrations', 'sql_templates', 'database_functions', 'child_health_monthly.sql'),
]


@periodic_task(run_every=crontab(minute=30, hour=23), acks_late=True, queue='icds_aggregation_queue')
def run_move_ucr_data_into_aggregation_tables_task(date=None):
    move_ucr_data_into_aggregation_tables.delay(date)


@serial_task('move-ucr-data-into-aggregate-tables', timeout=30 * 60, queue='icds_aggregation_queue')
def move_ucr_data_into_aggregation_tables(date=None, intervals=2):
    date = date or datetime.utcnow().date()
    monthly_dates = []

    # probably this should be run one time, for now I leave this in aggregations script (not a big cost)
    # but remove issues when someone add new table to mapping, also we don't need to add new rows manually
    # on production servers
    _update_ucr_table_mapping()

    first_day_of_month = date.replace(day=1)
    for interval in range(intervals - 1, 0, -1):
        # calculate the last day of the previous months to send to the aggregation script
        first_day_next_month = first_day_of_month - relativedelta(months=interval - 1)
        monthly_dates.append(first_day_next_month - relativedelta(days=1))

    monthly_dates.append(date)

    db_alias = get_icds_ucr_db_alias()
    if db_alias:
        with connections[db_alias].cursor() as cursor:
            _create_aggregate_functions(cursor)
            _update_aggregate_locations_tables(cursor)
            _create_child_health_monthly_view()

        tasks = []

        for monthly_date in monthly_dates:
            calculation_date = monthly_date.strftime('%Y-%m-%d')
            tasks.extend([
                group(
                    icds_aggregation_task.si(date=calculation_date, func=_update_months_table),
                    icds_aggregation_task.si(date=calculation_date, func=_aggregate_cf_forms),
                    icds_aggregation_task.si(date=calculation_date, func=_aggregate_thr_forms),
                    icds_aggregation_task.si(date=calculation_date, func=_aggregate_gm_forms),
                    icds_aggregation_task.si(date=calculation_date, func=_aggregate_child_health_pnc_forms),
                ),
                group(
                    icds_aggregation_task.si(date=calculation_date, func=_child_health_monthly_table),
                    icds_aggregation_task.si(date=calculation_date, func=_ccs_record_monthly_table),
                    icds_aggregation_task.si(date=calculation_date, func=_daily_attendance_table),
                ),
                group(
                    icds_aggregation_task.si(date=calculation_date, func=_agg_child_health_table),
                    icds_aggregation_task.si(date=calculation_date, func=_agg_ccs_record_table),
                ),
                group(
                    icds_aggregation_task.si(date=calculation_date, func=_agg_awc_table),
                    no_op_task_for_celery_bug.si(),
                )
            ])

        tasks.append(group(
            icds_aggregation_task.si(date=date.strftime('%Y-%m-%d'), func=aggregate_awc_daily),
            no_op_task_for_celery_bug.si(),
        ))
        tasks.append(group(
            email_dashboad_team.si(aggregation_date=date.strftime('%Y-%m-%d')),
            no_op_task_for_celery_bug.si(),
        ))
        chain(*tasks).delay()


@task(queue="icds_aggregation_queue")
def no_op_task_for_celery_bug():
    # Under celery 3.1.18, we've noticed that tasks need to be grouped when using canvas
    # If the tasks are not grouped, then once celery gets to that task, no future tasks will be queued.
    # Grouping one task by itself does not appear to work.
    # If there's only one task for a group, we can add this to get around this issue.
    # Once we upgrade celery we can experiment with getting rid of this.
    pass


def _create_aggregate_functions(cursor):
    try:
        celery_task_logger.info("Starting icds reports create_functions")
        for sql_function_path in SQL_FUNCTION_PATHS:
            path = os.path.join(os.path.dirname(__file__), *sql_function_path)
            with open(path, "r", encoding='utf-8') as sql_file:
                sql_to_execute = sql_file.read()
                cursor.execute(sql_to_execute)
        celery_task_logger.info("Ended icds reports create_functions")
    except Exception:
        # This is likely due to a change in the UCR models or aggregation script which should be rare
        # First step would be to look through this error to find what function is causing the error
        # and look for recent changes in this folder.
        _dashboard_team_soft_assert(False, "Unexpected occurred while creating functions in dashboard aggregation")
        raise


def _update_aggregate_locations_tables(cursor):
    try:
        path = os.path.join(os.path.dirname(__file__), 'sql_templates', 'update_locations_table.sql')
        celery_task_logger.info("Starting icds reports update_location_tables")
        with open(path, "r", encoding='utf-8') as sql_file:
            sql_to_execute = sql_file.read()
            cursor.execute(sql_to_execute)
        celery_task_logger.info("Ended icds reports update_location_tables_sql")
    except IntegrityError:
        # This has occurred when there's a location upload, but not all locations were updated.
        # Some more details are here https://github.com/dimagi/commcare-hq/pull/18839
        # It's usually fixed by rebuild the location UCR table and running this task again, but
        # that PR should fix that issue
        _dashboard_team_soft_assert(False, "Error occurred while aggregating locations")
        raise
    except Exception:
        # I'm not sure what this one will be
        _dashboard_team_soft_assert(
            False, "Unexpected occurred while aggregating locations in dashboard aggregation")
        raise


@task(queue='icds_aggregation_queue', bind=True, default_retry_delay=15 * 60, acks_late=True)
def icds_aggregation_task(self, date, func):
    db_alias = get_icds_ucr_db_alias()
    if not db_alias:
        return

    celery_task_logger.info("Starting icds reports {} {}".format(date, func.__name__))

    try:
        func(date)
    except Error as exc:
        _dashboard_team_soft_assert(
            False,
            "{} aggregation failed on {} for {}. This task will be retried in 15 minutes".format(
                func.__name__, settings.SERVER_ENVIRONMENT, date
            )
        )
        notify_exception(
            None, message="Error occurred during ICDS aggregation",
            details={'func': func.__name__, 'date': date, 'error': exc}
        )
        self.retry(exc=exc)

    celery_task_logger.info("Ended icds reports {} {}".format(date, func.__name__))


@track_time
def _aggregate_cf_forms(day):
    _state_based_aggregation(AggregateComplementaryFeedingForms, day)


@track_time
def _aggregate_gm_forms(day):
    _state_based_aggregation(AggregateGrowthMonitoringForms, day)


@track_time
def _aggregate_child_health_pnc_forms(day):
    _state_based_aggregation(AggregateChildHealthPostnatalCareForms, day)


@track_time
def _aggregate_thr_forms(day):
    _state_based_aggregation(AggregateChildHealthTHRForms, day)


def _state_based_aggregation(model, day):
    state_ids = (SQLLocation.objects
                 .filter(domain=DASHBOARD_DOMAIN, location_type__name='state')
                 .values_list('location_id', flat=True))

    agg_date = force_to_date(day)
    for state_id in state_ids:
        model.aggregate(state_id, agg_date)


@transaction.atomic
def _run_custom_sql_script(commands, day=None):
    db_alias = get_icds_ucr_db_alias()
    if not db_alias:
        return

    with connections[db_alias].cursor() as cursor:
        for command in commands:
            cursor.execute(command, [day])


@track_time
def _create_child_health_monthly_view():
    _run_custom_sql_script(["SELECT create_child_health_monthly_view()"])


@track_time
def aggregate_awc_daily(day):
    _run_custom_sql_script(["SELECT aggregate_awc_daily(%s)"], day)


@track_time
def _update_months_table(day):
    _run_custom_sql_script(["SELECT update_months_table(%s)"], day)


@track_time
def _child_health_monthly_table(day):
    _run_custom_sql_script([
        "SELECT create_new_table_for_month('child_health_monthly', %s)",
        "SELECT insert_into_child_health_monthly(%s)"
    ], day)


@track_time
def _ccs_record_monthly_table(day):
    _run_custom_sql_script([
        "SELECT create_new_table_for_month('ccs_record_monthly', %s)",
        "SELECT insert_into_ccs_record_monthly(%s)"
    ], day)


@track_time
def _daily_attendance_table(day):
    _run_custom_sql_script([
        "SELECT create_new_table_for_month('daily_attendance', %s)",
        "SELECT insert_into_daily_attendance(%s)"
    ], day)


@track_time
def _agg_child_health_table(day):
    _run_custom_sql_script([
        "SELECT create_new_aggregate_table_for_month('agg_child_health', %s)",
        "SELECT aggregate_child_health(%s)"
    ], day)


@track_time
def _agg_ccs_record_table(day):
    _run_custom_sql_script([
        "SELECT create_new_aggregate_table_for_month('agg_ccs_record', %s)",
        "SELECT aggregate_ccs_record(%s)"
    ], day)


@track_time
def _agg_awc_table(day):
    _run_custom_sql_script([
        "SELECT create_new_aggregate_table_for_month('agg_awc', %s)",
        "SELECT aggregate_awc_data(%s)"
    ], day)


@task(queue='icds_aggregation_queue')
def email_dashboad_team(aggregation_date):
    # temporary soft assert to verify it's completing
    _dashboard_team_soft_assert(False, "Aggregation completed on {}".format(settings.SERVER_ENVIRONMENT))
    celery_task_logger.info("Aggregation has completed")
    icds_data_validation.delay(aggregation_date)


@periodic_task(
    queue='background_queue',
    run_every=crontab(day_of_week='tuesday,thursday,saturday', minute=0, hour=21),
    acks_late=True
)
def recalculate_stagnant_cases():
    domain = 'icds-cas'
    config_ids = [
        'static-icds-cas-static-ccs_record_cases_monthly_v2',
        'static-icds-cas-static-ccs_record_cases_monthly_tableau_v2',
        'static-icds-cas-static-child_cases_monthly_v2',
        'static-icds-cas-static-child_cases_monthly_tableau_v2',
    ]

    stagnant_cases = set()

    for config_id in config_ids:
        config, is_static = get_datasource_config(config_id, domain)
        adapter = get_indicator_adapter(config)
        case_ids = _find_stagnant_cases(adapter)
        celery_task_logger.info(
            "Found {} stagnant cases in config {}".format(len(case_ids), config_id)
        )
        stagnant_cases = stagnant_cases.union(set(case_ids))
        celery_task_logger.info(
            "Total number of stagant cases is now {}".format(len(stagnant_cases))
        )

    case_accessor = CaseAccessors(domain)
    num_stagnant_cases = len(stagnant_cases)
    current_case_num = 0
    for case_ids in chunked(stagnant_cases, 1000):
        current_case_num += len(case_ids)
        cases = case_accessor.get_cases(list(case_ids))
        for case in cases:
            publish_case_saved(case, send_post_save_signal=False)
        celery_task_logger.info(
            "Resaved {} / {} cases".format(current_case_num, num_stagnant_cases)
        )


def _find_stagnant_cases(adapter):
    stagnant_date = datetime.utcnow() - timedelta(days=26)
    table = adapter.get_table()
    query = adapter.get_query_object()
    query = query.with_entities(table.columns.doc_id).filter(
        table.columns.inserted_at <= stagnant_date
    ).distinct()
    return query.all()


@task(queue='icds_dashboard_reports_queue')
def prepare_issnip_monthly_register_reports(domain, awcs, pdf_format, month, year, couch_user):
    selected_date = date(year, month, 1)
    report_context = {
        'reports': [],
        'user_have_access_to_features': icds_pre_release_features(couch_user),
    }

    pdf_files = []

    report_data = ISSNIPMonthlyReport(config={
        'awc_id': awcs,
        'month': selected_date,
        'domain': domain
    }, icds_feature_flag=icds_pre_release_features(couch_user)).to_pdf_format

    if pdf_format == 'one':
        report_context['reports'] = report_data
        cache_key = create_pdf_file(report_context)
    else:
        for data in report_data:
            report_context['reports'] = [data]
            pdf_hash = create_pdf_file(report_context)
            pdf_files.append({
                'uuid': pdf_hash,
                'location_name': data['awc_name']
            })
        cache_key = zip_folder(pdf_files)

    params = {
        'domain': domain,
        'uuid': cache_key,
        'format': pdf_format
    }

    return {
        'domain': domain,
        'uuid': cache_key,
        'format': pdf_format,
        'link': reverse('icds_download_pdf', params=params, absolute=True, kwargs={'domain': domain})
    }


@task(queue='background_queue')
def icds_data_validation(day):
    """Checks all AWCs to validate that there will be no inconsistencies in the
    reporting dashboard.
    """

    # agg tables store the month like YYYY-MM-01
    month = force_to_date(day)
    month.replace(day=1)
    return_values = ('state_name', 'district_name', 'block_name', 'supervisor_name', 'awc_name')

    bad_wasting_awcs = AggChildHealthMonthly.objects.filter(
        month=month, aggregation_level=5
    ).exclude(
        weighed_and_height_measured_in_month=(
            F('wasting_moderate') + F('wasting_severe') + F('wasting_normal')
        )
    ).values_list(*return_values)

    bad_stunting_awcs = AggChildHealthMonthly.objects.filter(month=month, aggregation_level=5).exclude(
        height_measured_in_month=(
            F('stunting_severe') + F('stunting_moderate') + F('stunting_normal')
        )
    ).values_list(*return_values)

    bad_underweight_awcs = AggChildHealthMonthly.objects.filter(month=month, aggregation_level=5).exclude(
        nutrition_status_weighed=(
            F('nutrition_status_normal') +
            F('nutrition_status_moderately_underweight') +
            F('nutrition_status_severely_underweight')
        )
    ).values_list(*return_values)

    bad_lbw_awcs = AggChildHealthMonthly.objects.filter(month=month, aggregation_level=5).exclude(
        weighed_and_born_in_month__gt=(
            F('low_birth_weight_in_month')
        )
    ).values_list(*return_values)

    _send_data_validation_email(
        return_values, month, {
            'bad_wasting_awcs': bad_wasting_awcs,
            'bad_stunting_awcs': bad_stunting_awcs,
            'bad_underweight_awcs': bad_underweight_awcs,
            'bad_lbw_awcs': bad_lbw_awcs,
        })


def _send_data_validation_email(csv_columns, month, bad_data):
    # intentionally using length here because the query will need to evaluate anyway to send the CSV file
    if all(len(v) == 0 for _, v in six.iteritems(bad_data)):
        return

    bad_wasting_awcs = bad_data.get('bad_wasting_awcs', [])
    bad_stunting_awcs = bad_data.get('bad_stunting_awcs', [])
    bad_underweight_awcs = bad_data.get('bad_underweight_awcs', [])
    bad_lbw_awcs = bad_data.get('bad_lbw_awcs', [])

    csv_file = io.StringIO()
    writer = csv.writer(csv_file)
    writer.writerow(('type',) + csv_columns)
    _icds_add_awcs_to_file(writer, 'wasting', bad_wasting_awcs)
    _icds_add_awcs_to_file(writer, 'stunting', bad_stunting_awcs)
    _icds_add_awcs_to_file(writer, 'underweight', bad_underweight_awcs)
    _icds_add_awcs_to_file(writer, 'low_birth_weight', bad_lbw_awcs)

    email_content = """
    Incorrect wasting AWCs: {bad_wasting_awcs}
    Incorrect stunting AWCs: {bad_stunting_awcs}
    Incorrect underweight AWCs: {bad_underweight_awcs}
    Incorrect low birth weight AWCs: {bad_lbw_awcs}

    Please see attached file for more details
    """.format(
        bad_wasting_awcs=len(bad_wasting_awcs),
        bad_stunting_awcs=len(bad_stunting_awcs),
        bad_underweight_awcs=len(bad_underweight_awcs),
        bad_lbw_awcs=len(bad_lbw_awcs),
    )

    filename = month.strftime('validation_results_%s.csv' % SERVER_DATE_FORMAT)
    send_HTML_email(
        '[{}] - ICDS Dashboard Validation Results'.format(settings.SERVER_ENVIRONMENT),
        DASHBOARD_TEAM_EMAILS, email_content,
        file_attachments=[{'file_obj': csv_file, 'title': filename, 'mimetype': 'text/csv'}],
    )


def _icds_add_awcs_to_file(csv_writer, error_type, rows):
    for row in rows:
        csv_writer.writerow((error_type, ) + row)


def _update_ucr_table_mapping():
    celery_task_logger.info("Started updating ucr_table_name_mapping table")
    for table in UCR_TABLE_NAME_MAPPING:
        if table.get('is_ucr', True):
            table_name = get_table_name(DASHBOARD_DOMAIN, table['name'])
        else:
            table_name = table['name']
        UcrTableNameMapping.objects.update_or_create(
            table_type=table['type'],
            defaults={'table_name': table_name}
        )
    celery_task_logger.info("Ended updating ucr_table_name_mapping table")
