hqDefine('domain/js/case_search', function() {
    var initialPageData = hqImport('hqwebapp/js/initial_page_data');
    $(function () {
        var CaseSearchConfig = hqImport('domain/js/case-search-config').CaseSearchConfig;
        var viewModel = new CaseSearchConfig({
            values: initialPageData.get('values'),
            caseTypes: initialPageData.get('case_types'),
        });
        $('#case-search-config').koApplyBindings(viewModel);
        $('#case-search-config').on('change', viewModel.change).on('click', ':button', viewModel.change);
    });
});
