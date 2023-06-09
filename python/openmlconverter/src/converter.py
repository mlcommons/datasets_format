"""Converting OpenML Datset into a DCF (Croissant) representation.

Typical usage:
  dcf = converter.convert(dataset_json, features_json)
"""

import datetime
import os
import re
from collections import OrderedDict
from functools import partial
from typing import Callable

import dateutil.parser
import langcodes


REQUIRED_FIELDS = ("name", "description")
BOOLEAN_STRING_VALUES = ({"TRUE", "FALSE"}, {"0", "1"})


def convert(openml_dataset: dict, openml_features: list[dict]) -> dict:
    """
    Convert an openml dataset into a DCF (Croissant) representation.

    Args:
        openml_dataset: A dictionary containing the dataset according to OpenML
        openml_features: A dictionary containing the features of the dataset according to OpenML

    Returns
        a dictionary with the DCF representation of the dataset.
    """
    _ds = partial(_get_field, json_dict=openml_dataset)  # get field from openml_dataset

    missing_fields = [field for field in REQUIRED_FIELDS if _ds(field=field) is None]
    if any(missing_fields):
        raise ValueError(f"Required fields missing: {' '.join(missing_fields)}.")

    croissant = OrderedDict()
    context = OrderedDict()
    croissant["@context"] = context
    context["@vocab"] = "https://schema.org/"
    context["sc"] = "https://schema.org/"
    context["ml"] = "http://mlcommons.org/schema/"
    context["recordSet"] = "ml:RecordSet"
    context["field"] = "ml:Field"
    context["dataType"] = "ml:dataType"
    context["source"] = "ml:source"

    croissant["@type"] = "sc:Dataset"
    croissant["@language"] = "en"
    croissant["name"] = _ds(field="name")
    croissant["description"] = _ds(field="description")
    croissant["version"] = _ds(field="version")
    croissant["creator"] = _ds(
        field="creator",
        transform=lambda v: [_person(p) for p in v] if isinstance(v, list) else _person(v),
    )
    croissant["contributor"] = _ds(field="contributor", transform=_person)
    croissant["dateCreated"] = _ds(field="upload_date", transform=dateutil.parser.parse)
    croissant["dateModified"] = _ds(field="processing_date", transform=dateutil.parser.parse)
    croissant["datePublished"] = _ds(field="collection_date", transform=_lenient_date_parser)
    croissant["inLanguage"] = _ds(field="language", transform=lambda v: langcodes.find(v).language)
    croissant["isAccessibleForFree"] = True
    croissant["license"] = _ds(field="licence")
    croissant["creativeWorkStatus"] = _ds(field="status")
    croissant["keywords"] = _ds(field="tag")
    croissant["citation"] = _ds(field="citation")
    croissant["sameAs"] = _ds(field="original_data_url")
    croissant["url"] = f"https://www.openml.org/search?type=data&id={_ds(field='id')}"

    # For now, we only add .arff files, not the .pq file, because we do not have a checksum for .pq
    distributions = [_file_object(url=_ds(field="url"), md5=_ds(field="md5_checksum"))]
    distribution_source = distributions[0]["name"]
    croissant["distribution"] = distributions

    record_set = OrderedDict()
    croissant["recordSet"] = [record_set]
    record_set["name"] = _ds(field="name", transform=_sanitize_name_string) + "_records"
    record_set["@type"] = "ml:RecordSet"
    record_set["source"] = f"#{{{distribution_source}}}"
    record_set["key"] = _row_identifier(openml_features, distribution_source)
    record_set["field"] = []
    for feature in openml_features:
        field = OrderedDict()
        record_set["field"].append(field)
        field["name"] = _sanitize_name_string(feature["name"])
        field["@type"] = "ml:Field"
        field["dataType"] = _datatype(feature["data_type"], feature.get("nominal_value", None))
        field["source"] = f"#{{{distribution_source}/{feature['name']}}}"
        # TODO: handling special characters such as '/' in column names
    _remove_empty_values(croissant)
    return croissant


def _get_field(json_dict: dict, field: str, transform: Callable | None = None):
    """
    Get a field from a dictionary optionally perform the transformation.

    This is a convenience function. If field does not exist, throw an error if the field is
    required, otherwise return None.

    Args:
        json_dict: Any dictionary.
        field: A string containing the field name
        transform: A function to be applied to the resulting value. If None, no transformation
          will be applied.

    Returns:
        The value of the field, whereby the transformation (if any) is applied, or None if the
        field is not present and not required.

    Raises:
        ValueError: Unknown date/datetime formatL: [format].
        ValueError: Unrecognized file extension in url: [url].
        ValueError: Unrecognized datatype: [openml_datatype].
    """
    if field not in json_dict:
        return None
    val = json_dict[field]
    if transform is not None:
        return transform(val)
    return val


def _sanitize_name_string(name: str) -> str:
    """Replace special characters with underscores, and transform to lower case.

    Args:
        name: a name of a json-ld object.

    Returns:
        a sanitized version of the name
    """
    return re.sub("[^A-Za-z0-9]", "_", name).lower()


def _person(name: str) -> dict | None:
    """
    A dictionary with json-ld fields for a https://schema.org/Person

    Args:
        name: The name of the person

    Returns:
        A dictionary with json-ld fields for a schema.org Person, or None if the name is not
        present.
    """
    if not name:
        return None
    person = OrderedDict()
    person["@context"] = "https://schema.org"
    person["@type"] = "sc:Person"
    person["name"] = name
    return person


def _file_object(url: str, md5: str) -> dict | None:
    """
    A dictionary with json-ld fields for a FileObject.

    The FileObject is defined in the MLCommons DCF (Croissant) schema and based on
    https://schema.org/CreativeWork.

    Args:
        url: The url of the FileObject
        md5: The md5 checksum of the FileObject

    Returns:
        A dictionary with json-ld fields for a FileObject, or None if the URL is None.

    Raises:
        ValueError: Unrecognized file extension in url: [url].
    """
    if not url:
        return None
    filename = os.path.split(url)[-1]
    if filename.endswith(".arff"):
        mimetype = "text/plain"  # No official arff mimetype exists
    elif filename.endswith(".pq"):
        mimetype = "application/vnd.apache.parquet"  # Not an official mimetype yet
        # see https://issues.apache.org/jira/browse/PARQUET-1889
    else:
        raise ValueError(f"Unrecognized file extension in url: {url}")
    file_object = OrderedDict()
    file_object["name"] = filename
    file_object["@type"] = "sc:FileObject"
    file_object["contentUrl"] = url
    file_object["encodingFormat"] = mimetype
    file_object["md5"] = md5

    # TODO: add sameAs (other distribution)?
    return file_object


def _datatype(openml_datatype: str, nominal_values: list[str] | None) -> str:
    """
    Convert the datatype according to OpenML to a schema.org datatype.

    In DCF schema.org datatypes are used on default.

    Args:
        openml_datatype: The datatype according to OpenML
        nominal_values: An optional list of strings with the possible values.

    Returns:
        The schema.org datatype.

    Raises:
        ValueError: Unknown datatype: [openml_datatype].
    """
    if nominal_values and any(
        set(nominal_values).issubset(booleans) for booleans in BOOLEAN_STRING_VALUES
    ):
        return "Boolean"
    d_type = {
        "numeric": "Number",
        "string": "Text",
        "nominal": "Text",  # TODO: where to add the possible values?
    }.get(openml_datatype, None)
    if d_type is None:
        raise ValueError(f"Unknown datatype: {openml_datatype}.")
    return d_type


def _row_identifier(
    openml_features: list[dict[str, any]], distribution_source: str
) -> list[str] | str | None:
    """
    Determine the feature names that uniquely identify a row.

    Example features:
    [
        {
            "index": "1",
            "name": "survived",
            "data_type": "nominal",
            "nominal_value": [
                "0",
                "1"
            ],
            "is_target": "true",
            "is_ignore": "false",
            "is_row_identifier": "false",
            "number_of_missing_values": "0"
        }, [...]
    ]


    Args:
        openml_features: A list of dictionaries containing the OpenML description of features.
        distribution_source: The name of the distribution.

    Returns:
        A list of feature names, a single feature name, or None.
    """
    row_identifiers = [
        f"#{{{_sanitize_name_string(f['name'])}}}"
        for f in openml_features
        if f["is_row_identifier"] == "true"
    ]
    if len(row_identifiers) > 1:
        return row_identifiers
    return next(iter(row_identifiers), None)


def _lenient_date_parser(value: str) -> datetime.date | datetime.datetime:
    """
    Try to parse the value as a date or datetime.

    This can handle any string that dateutil.parser can parse, such as "2000-01-01T00:00:00",
    but also only a year, such as "2000"

    Args:
        value: a date-like string

    Returns:
        A datetime if the date and time are specified, or a date if the time is not specified.

    Raises:
        dateutil.parser.ParserError: Unknown date/datetime format.
    """
    if len(value) == len("YYYY") and (value.startswith("19") or value.startswith("20")):
        year = int(value)
        return datetime.date(year, 1, 1)
    return dateutil.parser.parse(value)


def _remove_empty_values(dct_: dict):
    """
    In-line function to recursively remove fields that have value None or an empty list/dict.

    Args:
        dct_: a dictionary
    """
    for key, value in list(dct_.items()):
        if isinstance(value, dict):
            _remove_empty_values(value)
            if len(value) == 0:
                del dct_[key]
        elif isinstance(value, list):
            for list_item in value:
                if isinstance(list_item, dict):
                    _remove_empty_values(list_item)
            if len(value) == 0:
                del dct_[key]
        elif value is None:
            del dct_[key]
