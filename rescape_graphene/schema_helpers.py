import inspect
from decimal import Decimal

import graphene
from . import ramda as R
from django.contrib.postgres.fields import JSONField
from django.db.models import AutoField, CharField, BooleanField, BigAutoField, DecimalField, \
    DateTimeField, DateField, BinaryField, TimeField, FloatField, EmailField, UUIDField, TextField, IntegerField, \
    BigIntegerField, NullBooleanField
from graphene import Scalar, InputObjectType
from graphql.language import ast
from inflection import camelize
# Indicates a CRUD operation is not allowed to use this field
from .graphene_helpers import dump_graphql_keys, dump_graphql_data_object
from .memoize import memoize

DENY = 'deny'
# Indicates a CRUD operation is required to use this field
REQUIRE = 'require'
# Indicates a CRUD operation is required to use this field and it is used as a match on uniqueness
# with 0 more other fields.
# For instance, username and email are both marked update=[REQUIRE, UNIQUE], they are both used to check uniqueness
# when updating, using:
# User.objects.update_or_create(defaults=**kwargs, username='foo', email='foo@foo.foo')
# Then it would take an admin mutation to update the username and/or email, such as:
# User.objects.update_or_create(defaults={username: 'boo', email='boo@boo.boo'}, id=1234231)
# Similarly data_points fields like blockname can't be updated except by an admin mutation
# All fields that are REQUIRE_UNIQUE should probably always match a database constraint for those fields
UNIQUE = 'unique'
# UNIQUE primary key
PRIMARY = 'primary'

# Indicates a CRUD operation is can optionally use this field
ALLOW = 'allow'

CREATE = 'create'
READ = 'read'
UPDATE = 'update'
DELETE = 'delete'


# https://github.com/graphql-python/graphene-django/issues/91
class Decimal(Scalar):
    """
    The `Decimal` scalar type represents a python Decimal.
    """

    @staticmethod
    def serialize(dec):
        assert isinstance(dec, Decimal), (
            'Received not compatible Decimal "{}"'.format(repr(dec))
        )
        return str(dec)

    @staticmethod
    def parse_value(value):
        return Decimal(value)

    @classmethod
    def parse_literal(cls, node):
        if isinstance(node, ast.StringValue):
            return cls.parse_value(node.value)


class DataPointRelatedCreateInputType(InputObjectType):
    id = graphene.String(required=True)


@memoize(map_args=lambda args: [args[0]['graphene_type'], args[1]])
def input_type_class(field_dict_value, crud):
    """
    An InputObjectType subclass for use as nested query argument types and mutation argument types
    The subclass is dynamically created based on the field_dict_value['graphene_type'] and the crud type.
    The fields are based on field_dicta_values['fields'] and the underlying Django model of the graphene_type,
    as well as the rules for the crud type spceified in field_dict_vale.
    :param field_dict_value:
    :param crud: CREATE, UPDATE, or READ
    :return: An InputObjectType subclass
    """
    graphene_class = field_dict_value['graphene_type']
    return type(
        '%sRelated%sInputType' % (graphene_class.__name__, camelize(crud, True)),
        (InputObjectType,),
        # Create Graphene types for the InputType based on the field_dict_value.fields
        # This will typically just be an id field to reference an existing object.
        # If the graphene type is based on a Django model, the Django model fields are merged with it,
        # otherwise it's assumed that field_dict_value['fields'] are independent of a Django model and
        # each have their own type property
        # It could be used to create a dependent object, for instance creating a UserPreference instance
        # on a User instance
        input_type_fields(
            merge_with_django_properties(
                field_dict_value['graphene_type'],
                field_dict_value['fields']
            ) if hasattr(graphene_class._meta, 'model') else
            field_dict_value['fields'],
            crud
        )
    )


def related_input_field(field_dict_value, *args, **kwargs):
    """
        Make an InputType subclass based on a Graphene type
    :param field_dict_value: The field dict value for a graphene field. This must exist and have a graphene_type
    that matches the Django model and it must have a fields property that is a field_dict for that relation
    Example. If The relation is data_point, the model is DataPoint and the graphene_type is DataPointType
    and fields could be dict(
        id=dict(create=REQUIRE, update=DENY),
    )
    meaning that only the id can be specified for the DataPoint relationship to resolve an existing DataPoint
    :param args:
    :param kwargs:
    :return: A lambda for A Graphene Field to create the InputType subclass. The lambda needs a crud type
    """
    return lambda crud: graphene.InputField(input_type_class(field_dict_value, crud), *args, **kwargs)


@R.curry
def related_input_field_for_crud_type(field_dict_value, crud):
    """
        Resolved the foreign key input field for the given crud type
    :param field: The Django Field
    :param field_dict_value: The corresponding field dict value. This must exist and have a graphene_type
    that matches the Django model and it must have a fields property that is a field_dict for that relation
    :param crud: CREATE or UPDATE
    :return:
    """
    return lambda *args, **kwargs: related_input_field(field_dict_value, *args, **kwargs)(crud)


def django_to_graphene_type(field, field_dict_value):
    """
        Resolve the actual AutoField type. I can't find a good way to do this
    :param field: The Django Field
    :param field_dict_value: The corresponding field_dict value if it exists. This required for related fields.
    For related fields it must containt field_dict_value.graphene_type, which is the graphene type for that field
    as well as a fields property which is the fields_dict for that field
    :return:
    """
    if R.prop_or(False, 'graphene_type', field_dict_value or {}):
        # This is detected as a lambda and called first with crud to establish what fields are needed in the
        # dynamic InputField subclass. Then another lambda is returned expecting args and kwargs, just like
        # the other graphene types above
        return related_input_field_for_crud_type(field_dict_value)

    return {
        AutoField: graphene.Int,
        IntegerField: graphene.Int,
        BigAutoField: graphene.Int,
        CharField: graphene.String,
        BigIntegerField: graphene.Int,
        BinaryField: graphene.Int,
        BooleanField: graphene.Boolean,
        NullBooleanField: graphene.Boolean,
        DateField: graphene.Date,
        DateTimeField: graphene.DateTime,
        TimeField: graphene.Time,
        DecimalField: Decimal,
        FloatField: graphene.Float,
        EmailField: graphene.String,
        UUIDField: graphene.UUID,
        TextField: graphene.String,
        JSONField: graphene.JSONString
    }[field.__class__]


def process_field(field_to_unique_field_groups, field, field_dict_value):
    """
        Process Django field for important properties like type and uniqueness
    :param field_to_unique_field_groups:
    :param field: The Django Field
    :param field_dict_value: The matching field_dict_value if it exists. This is only used for related fields
    That need fields for making an InputType subclass. When used the field_dict_value
    must have a graphene_type property that is a graphene type and fields property that is a field_dict for that relation
    or a type property that resolves to a graphene type whose fields are the same no matter the crud operation
    :return: A dict with the unique property and anything else we need
    """
    unique = R.compact([
        PRIMARY if field.primary_key else None,
        UNIQUE if field.unique else None,
        R.prop_or(None, field.attname, field_to_unique_field_groups)
    ])
    # Normally the field_dict_value will delegate the type to the underlying Django model
    # In cases where we need an explicit type, becsuse the field represents something modeled differently than django,
    # we specify the type property on field_dict_value, which takes precedence
    return dict(
        type=django_to_graphene_type(field, field_dict_value),
        unique=unique
    )


def parse_django_class(model, field_dict):
    """
        Parse the fields of a Django model to merge important properties with
        a graphene field_dict
    :param model: The Django model
    :param field_dict: The field_dict, which is only needed to supplies the fields to related fields. Related
    fields are made into InputType subclasses for mutations, so field_dict[field]['fields'] supplies the fields
    for the InputType. The fields are in the same format as field_dict
    :return:
    """
    # This mess just maps each attr to all "unique together" tuples it's in
    field_to_unique_field_groups = R.from_pairs_to_array_values(
        R.flatten(
            R.map(
                lambda uniq_field_group:
                R.map(
                    lambda attrname: [attrname, R.join(',', uniq_field_group)],
                    uniq_field_group
                ),
                model._meta.unique_together
            )
        )
    )
    return R.from_pairs(R.map(
        lambda field: [
            # Key by file.name
            field.name,
            # Process each field
            process_field(field_to_unique_field_groups, field, R.prop(field.name, field_dict))
        ],
        # Only accept model fields that are defined in field_dict
        R.filter(
            lambda field: field.name in field_dict,
            model._meta.fields
        )
    ))


def merge_with_django_properties(graphene_type, field_dict):
    """
        Merges a field_dict with Graphene fields and other options with relevant Django model properties.
        Only Django properties in the field_dict are merged
        This results in a dict keyed by field that can be used for generating graphql queries and resolvers
    :param graphene_type:
    :param field_dict:
    :return:
    """
    return R.merge_deep(
        field_dict,
        R.pick(
            R.keys(field_dict),
            parse_django_class(graphene_type._meta.model, field_dict))
    )


def allowed_query_arguments(fields_dict):
    """
        Returns fields that can be queried
    :param fields_dict: The fields_dict for the Django model
    :return:
    """
    return R.map_dict(
        lambda value:
            # If the type is a scalar, just instantiate
            R.prop('type', value)() if inspect.isclass(R.prop('type', value)) and issubclass(R.prop('type', value), Scalar) else
            # Otherwise created a related field InputType subclass. In order to query a nested object, it has to
            # be an input field. Example: If A User has a Group, we can query for users named 'Peter' who are admins:
            # graphql: users: (name: "Peter", group: {role: "admin"})
            # https://github.com/graphql-python/graphene/issues/431
            input_type_class(value, READ)(),
        R.filter_dict(
            lambda key_value:
            # Only; accept Scalars. We don't need Relations because they are done automatically by graphene
            # inspect.isclass(R.prop('type', value)) and issubclass(R.prop('type', value), Scalar) and
            # Don't allow DENYd READs
            R.not_func(R.prop_eq_or_in(READ, DENY, key_value[1])),
            fields_dict
        )
    )


def guess_update_or_create(fields_dict):
    """
    Determines if the query is intended to be a create or update
    :param fields:
    :return:
    """
    if R.has('id', fields_dict):
        return UPDATE
    # Guessing create. This might still be an update if unique fields are used
    return CREATE


def instantiate_graphene_type(value, crud):
    """
        Instantiates the Graphene type at value.type
    :param value: Dict containing type and possible crud fields like value['create'] and value['update']
    These optional values indicate if a field is required
    :param crud:
    :return:
    """
    graphene_type = R.prop('type', value)
    # If a lambda is returned we have an InputType subclass that needs to know the crud type
    resolved_graphene_type = graphene_type(crud) if R.isfunction(graphene_type) else graphene_type
    # Instantiate
    return resolved_graphene_type(
        # Add required depending on whether this is an insert or update
        # This means if a user omits these fields an error will occur
        required=R.prop_eq_or_in_or(False, crud, REQUIRE, value)
    )


def input_type_fields(fields_dict, crud=None):
    """
    :param fields_dict: The fields_dict for the Django model
    :param crud: INSERT, UPDATE, or DELETE. If not specified, guess based on the fields given
    :return:
    """
    crud = crud or guess_update_or_create(fields_dict)
    return R.map_dict(
        lambda value: instantiate_graphene_type(value, crud),
        # Filter out values that are deny
        # This means that if the user tries to pass these fields to graphql an error will occur
        R.filter_dict(
            lambda key_value: R.not_func(R.prop_eq_or(False, crud, DENY, key_value[1])),
            fields_dict
        )
    )


def input_type_parameters_for_update_or_create(fields_dict, values):
    """
        Returns the input_type fields for a mutation class in the form
        {
            defaults: {...}
            unique_fields
        }
        where the default fields are any fields that can be updated or inserted if the object is new
        and unique_fields are any fields are used for uniqueness check by Django's update_or_create.
        if nothing matches all unique_fields then update_or_create combines all fields and does an insert
    :param fields_dict: The fields_dict for the Django model
    :param values: field name and value dict
    :return:
    """

    return dict(
        # defaults are for updating/inserting
        defaults=R.filter_dict(
            lambda key_value: R.not_func(R.length(R.item_path([key_value[0], 'unique'], fields_dict))),
            values
        ),
        # rest are for checking uniqueness and if unique for inserting as well
        # this matches Django Query's update_or_create
        **R.filter_dict(
            lambda key_value: R.length(R.item_path([key_value[0], 'unique'], fields_dict)),
            values
        )
    )


@R.curry
def graphql_query(query_name, fields):
    """
        Creates a query based on the name and given fields
    :param query_name:
    :param fields:
    :returns A lambda that expects a Graphene client, optional variable_definitions, and **kwargs that contain kwargs
    for the client.execute call, such as any of
        context_value={'user': 'Peter'},  root_value={'user': 'Peter'}, variable_value={'user': 'Peter'}
        variable_definitions, if specified should match the query form: e.g. dict(id='String') where the key
        is the field and the value is the type. This results in query whatever(id: String!) { query_name(id: id) ... }
    """

    def form_query(client, variable_definitions={}, field_overrides={}, **kwargs):
        """
        # Make definitions in the form id: String!, foo: Int!, etc
        :param client:
        :param variable_definitions:
        :param field_overrides: Override the fields argument with limited fields in the same format as fields above
        :return:
        """
        formatted_definitions = R.join(
            ', ',
            R.values(
                R.map_with_obj(
                    lambda key, value: '$%s: %s!' % (key, value),
                    variable_definitions
                )
            )
        )
        query = '''query someMadeUpString%s { 
                %s%s {
                    %s
                }
            }''' % (
            '(%s)' % formatted_definitions if formatted_definitions else '',
            query_name,
            '(%s)' %
            R.join(
                ', ',
                # Put the variable definitions in (x: $x, y: $y, etc) if variable definitions exist
                R.map(
                    lambda key: '%s: $%s' % (key, key),
                    R.keys(variable_definitions))
            ) if variable_definitions else '',
            dump_graphql_keys(field_overrides or fields)
        )
        return client.execute(query, **kwargs)

    return form_query


@R.curry
def graphql_update_or_create(mutation_config, fields, client, values):
    """
        Update or create by creating a graphql mutation
    :param mutation_config: A config in the form
        class_name='User'|'DataPoint'|etc
        crud={
            CREATE: 'create*',
            UPDATE: 'update*'
        },
        resolve=guess_update_or_create
        where * is the name of the model, such as User. The resolve function returns CREATE or UPDATE
        based on what is in values. For instance, it guesses that passing an id means the user wants to update
    :param fields: A dict of field names field definitions, such as that in user_schema. The keys can be
    Django/python style slugged or graphql camel case. They will be converted to graphql style
    :param client: Graphene client
    :param values: key values of what to update. keys can be slugs or camel case. They will be converted to camel
    :return:
    """
    update_or_create = guess_update_or_create(values)
    # We name the mustation classNameMutation and the parameter classNameData
    # where className is the camel-case version of the given class name in mutation_config.class_name
    name = camelize(R.prop('class_name', mutation_config), False)
    return client.execute(''' 
        mutation %sMutation {
            %s(%sData: %s) {
                %s {
                    %s 
                }
            }
        }''' % (
        name,
        # This will be createClass or updateClass where class is the class name
        R.item_path(['crud', update_or_create], mutation_config),
        name,
        # Key values for what is being created or updated
        dump_graphql_data_object(values),
        name,
        dump_graphql_keys(fields)
    ))
