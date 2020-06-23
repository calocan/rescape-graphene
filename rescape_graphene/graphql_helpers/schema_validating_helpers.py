from inflection import underscore
from rescape_python_helpers import ramda as R
from rescape_python_helpers.functional.ramda import pick_deep

from rescape_graphene import process_filter_kwargs


def quiz_model_query(client, model_query_function, result_name, variables):
    """
        Tests a query for a model with variables that produce exactly one result
    :param client: Apollo client
    :param model_query_function: Query function expecting the client and variables
    :param result_name: The name of the result object in the data object
    :param variables: key value variables for the query
    :return: void
    """
    all_result = model_query_function(client)
    assert not R.has('errors', all_result), R.dump_json(R.prop('errors', all_result))
    result = model_query_function(
        client,
        variables=variables
    )
    # Check against errors
    assert not R.has('errors', result), R.dump_json(R.prop('errors', result))
    # Simple assertion that the query looks good
    assert 1 == R.length(R.item_path(['data', result_name], result))


def quiz_model_paginated_query(client, model_class, paginated_query, result_name, props, omit_props):
    """
        Tests a pagination query for a model with variables
    :param client: Apollo client
    :param model_class: Model class
    :param paginated_query: Model's pagination query
    :param result_name: The name of the results in data.[result_name].objects
    :param props: The props to query, not including pagination
    :param omit_props: Props to omit from assertions because they are nondeterminate
    :return:
    """
    result = paginated_query(
        client,
        variables=dict(
            page=1,
            page_size=1,
            objects=props
        )
    )
    # Check against errors
    assert not R.has('errors', result), R.dump_json(R.prop('errors', result))
    first_page_objects = R.item_path(['data', result_name, 'objects'], result)
    # Assert we got 1 result because our page is size 1
    assert 1 == R.compose(
        R.length,
        R.map(R.omit(omit_props)),
    )(first_page_objects)

    remaining_oslo_object_id = R.head(
        list(
            set(
                R.map(
                    R.prop('id'),
                    model_class.objects.filter(
                        **process_filter_kwargs(props)
                    )
                )
            ) -
            set(R.map(R.compose(int, R.prop('id')), first_page_objects))
        )
    )

    page_info = R.item_path(['data', result_name], result)
    # We have 1 page pages and thus expect 2 pages that match Oslo
    assert page_info['pages'] == 2
    assert page_info['hasNext'] == True
    assert page_info['hasPrev'] == False
    # Get the next page
    new_result = paginated_query(
        client,
        variables=dict(
            page=page_info['page'] + 1,
            page_size=page_info['pageSize'],
            objects=props
        )
    )
    assert remaining_oslo_object_id == R.compose(
        lambda id: int(id),
        R.item_path(['data', result_name, 'objects', 0, 'id'])
    )(new_result)

    new_page_info = R.item_path(['data', result_name], new_result)
    assert new_page_info['pages'] == 2
    assert new_page_info['hasNext'] == False
    assert new_page_info['hasPrev'] == True


def quiz_model_mutation_create(client, graphql_update_or_create_function, result_path, values,
                               second_create_results=None, second_create_does_update=False):
    """
        Tests a create mutation for a model
    :param client: The Apollo Client
    :param graphql_update_or_create_function: The update or create mutation function for the model. Expects client and input values
    :param result_path: The path to the result of the create in the data object (e.g. createRegion.region)
    :param values: The input values to use for the create
    :param second_create_results: Tests a second create if specified. Use to make sure that create with the same values
    creates a new instance or updates, depending on what you expect it to do.
    :param second_create_does_update: Default False. If True expects a second create with the same value to update rather than create a new instance
    :return: Tuple with two return values. The second is null if second_create_results is False
    """
    result = graphql_update_or_create_function(client, values=values)

    result_path_partial = R.item_str_path(f'data.{result_path}')
    assert not R.has('errors', result), R.dump_json(R.prop('errors', result))
    # Get the created value, using underscore to make the camelcase keys match python keys
    created = R.map_keys(
        lambda key: underscore(key),
        result_path_partial(result)
    )
    # get all the keys in values that are in created. This should match values if created has everything we expect
    assert values == pick_deep(created, values)
    # Try creating with the same values again, unique constraints will apply to force a create or an update will occur
    if second_create_results:
        new_result = graphql_update_or_create_function(client, values)
        assert not R.has('errors', new_result), R.dump_json(R.prop('errors', new_result))
        created_too = result_path_partial(new_result)
        if second_create_does_update:
            assert created['id'] == created_too['id']
        if not second_create_does_update:
            assert created['id'] != created_too['id']
        assert second_create_results == pick_deep(second_create_results, created_too)
    else:
        new_result = None

    return result, new_result


def quiz_model_mutation_update(client, graphql_update_or_create_function, create_path, update_path, values,
                               update_values):
    """
        Tests an update mutation for a model by calling a create with the given values then an update
        with the given update_values (plus the create id)
    :param client: The Apollo Client
    :param graphql_update_or_create_function: The update or create mutation function for the model. Expects client and input values
    :param create_path: The path to the result of the create in the data object (e.g. createRegion.region)
    :param update_path: The path to the result of the update in the data object (e.g. updateRegion.region)
    :param values: The input values to use for the create
    :param update_values: The input values to use for the update. This can be as little as one key value
    :return:
    """
    result = graphql_update_or_create_function(client, values=values)
    assert not R.has('errors', result), R.dump_json(R.prop('errors', result))
    # Extract the result and map the graphql keys to match the python keys
    created = R.compose(
        lambda r: R.map_keys(lambda key: underscore(key), r),
        lambda r: R.item_str_path(f'data.{create_path}', r)
    )(result)
    # look at the users added and omit the non-determinant dateJoined
    assert values == pick_deep(created, values)
    # Update with the id and optionally key if there is one + update_values
    update_result = graphql_update_or_create_function(
        client,
        R.merge_all([
            dict(
                id=int(created['id'])
            ),
            dict(
                key=created['key']
            ) if R.prop_or(False, 'key', created) else {},
            update_values
        ])
    )
    assert not R.has('errors', update_result), R.dump_json(R.prop('errors', update_result))
    updated = R.item_str_path(f'data.{update_path}', update_result)
    assert created['id'] == updated['id']
    assert update_values == pick_deep(
        update_values,
        updated
    )
    return result, update_result
