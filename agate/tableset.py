#!/usr/bin/env python

"""
This module contains the :class:`TableSet` class which abstracts an set of
related tables into a single data structure. The most common way of creating a
:class:`TableSet` is using the :meth:`.Table.group_by` method, which is
similar to SQL's ``GROUP BY`` keyword. The resulting set of tables each have
identical columns structure.

:class:`TableSet` functions as a dictionary. Individual tables in the set can
be accessed by using their name as a key. If the table set was created using
:meth:`.Table.group_by` then the names of the tables will be the group factors
found in the original data.

:class:`TableSet` replicates the majority of the features of :class:`.Table`.
When methods such as :meth:`TableSet.select`, :meth:`TableSet.where` or
:meth:`TableSet.order_by` are used, the operation is applied to *each* table
in the set and the result is a new :class:`TableSet` instance made up of
entirely new :class:`.Table` instances.

:class:`TableSet` instances can also contain other TableSet's. This means you
can chain calls to :class:`.Table.aggregate` and :class:`TableSet.aggregeate`
and end up with data aggregated across multiple dimensions.
"""

from collections import Mapping
from copy import copy
from glob import glob
import os

try:
    from collections import OrderedDict
except ImportError: # pragma: no cover
    from ordereddict import OrderedDict

from agate.aggregations import Aggregation
from agate.data_types import Text, TypeTester
from agate.exceptions import ColumnDoesNotExistError
from agate.rows import RowSequence

class TableMethodProxy(object):
    """
    A proxy for :class:`TableSet` methods that converts them to individual
    calls on each :class:`.Table` in the set.
    """
    def __init__(self, tableset, method_name):
        self.tableset = tableset
        self.method_name = method_name

    def __call__(self, *args, **kwargs):
        groups = OrderedDict()

        for key, value in self.tableset._tables.items():
            groups[key] = getattr(value, self.method_name)(*args, **kwargs)

        return TableSet(groups, key_name=self.tableset._key_name)

class TableSet(Mapping):
    """
    An group of named tables with identical column definitions. Supports
    (almost) all the same operations as :class:`.Table`. When executed on a
    :class:`TableSet`, any operation that would have returned a new
    :class:`.Table` instead returns a new :class:`TableSet`. Any operation
    that would have returned a single value instead returns a dictionary of
    values.

    :param tables: A dictionary of string keys and :class:`Table` values.
    :param key_name: A name that describes the grouping properties. Used as
        the column header when the groups are aggregated. Defaults to the
        column name that was grouped on.
    :param key_type: An instance some subclass of :class:`.DataType`. If not
        provided it will default to a :class`.Text`.
    """
    def __init__(self, group, key_name='group', key_type=None):
        self._key_name = key_name
        self._key_type = key_type or Text()

        # Note: list call is a workaround for Python 3 "ValuesView"
        self._sample_table = list(group.values())[0]

        while isinstance(self._sample_table, TableSet):
            self._sample_table = list(self._sample_table.values())[0]

        self._column_types = self._sample_table.get_column_types()
        self._column_names = self._sample_table.get_column_names()

        for name, table in group.items():
            if table._column_types != self._column_types:
                raise ValueError('Not all tables have the same column types!')

            if table._column_names != self._column_names:
                raise ValueError('Not all tables have the same column names!')

        self._tables = copy(group)

        self.select = TableMethodProxy(self, 'select')
        self.where = TableMethodProxy(self, 'where')
        self.find = TableMethodProxy(self, 'find')
        self.stdev_outliers = TableMethodProxy(self, 'stdev_outliers')
        self.mad_outliers = TableMethodProxy(self, 'mad_outliers')
        self.pearson_correlation = TableMethodProxy(self, 'pearson_correlation')
        self.order_by = TableMethodProxy(self, 'order_by')
        self.limit = TableMethodProxy(self, 'limit')
        self.distinct = TableMethodProxy(self, 'distinct')
        self.inner_join = TableMethodProxy(self, 'inner_join')
        self.left_outer_join = TableMethodProxy(self, 'left_outer_join')
        self.group_by = TableMethodProxy(self, 'group_by')
        self.compute = TableMethodProxy(self, 'compute')
        self.percent_change = TableMethodProxy(self, 'percent_change')
        self.rank = TableMethodProxy(self, 'rank')
        self.z_scores = TableMethodProxy(self, 'z_scores')

    def __getitem__(self, k):
        return self._tables.__getitem__(k)

    def __iter__(self):
        return self._tables.__iter__()

    def __len__(self):
        return self._tables.__len__()

    @classmethod
    def from_csv(cls, dir_path, column_info, header=True, **kwargs):
        """
        Create a new :class:`TableSet` from a directory of CSVs. This method
        will use csvkit if it is available, otherwise it will use Python's
        builtin csv module.

        ``kwargs`` will be passed through to :meth:`csv.reader`.

        If you are using Python 2 and not using csvkit, this method is not
        unicode-safe.

        :param dir_path: Path to a directory full of CSV files. All CSV files
            in this directory will be loaded.
        :param column_info: A sequence of pairs of column names and types. The latter
            must be instances of :class:`.DataType`. Or, an instance of
            :class:`.TypeTester` to infer types.
        :param header: If `True`, the first row of the CSV is assumed to contains
            headers and will be skipped.
        """
        from agate.table import Table

        use_inference = isinstance(column_info, TypeTester)

        if use_inference and not header:
            raise ValueError('Can not apply TypeTester to a CSV without headers.')

        if not os.path.isdir(dir_path):
            raise IOError('Specified path doesn\'t exist or isn\'t a directory.')

        tables = OrderedDict()

        if use_inference:
            has_inferred_columns = False

        for path in glob(os.path.join(dir_path, '*.csv')):
            name = os.path.split(path)[1].strip('.csv')

            table = Table.from_csv(path, column_info, header=header, **kwargs)

            if use_inference and not has_inferred_columns:
                column_info = tuple(zip(table.get_column_names(), table.get_column_types()))
                has_inferred_columns = True

            tables[name] = table

        return TableSet(tables)

    def to_csv(self, dir_path, **kwargs):
        """
        Write this each table in this set to a separate CSV in a given
        directory. This method will use csvkit if it is available, otherwise
        it will use Python's builtin csv module.

        ``kwargs`` will be passed through to :meth:`csv.writer`.

        If you are using Python 2 and not using csvkit, this method is not
        unicode-safe.

        :param dir_path: Path to the directory to write the CSV files to.
        """
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)

        for name, table in self._tables.items():
            path = os.path.join(dir_path, '%s.csv' % name)

            table.to_csv(path, **kwargs)

    def get_column_types(self):
        """
        Get an ordered list of this :class:`.TableSet`'s column types.

        :returns: A :class:`tuple` of :class:`.Column` instances.
        """
        return self._column_types

    def get_column_names(self):
        """
        Get an ordered list of this :class:`TableSet`'s column names.

        :returns: A :class:`tuple` of strings.
        """
        return self._column_names

    def _aggregate(self, aggregations=[]):
        """
        Recursive aggregation allowing for TableSet's to be nested inside
        one another.

        See :meth:`TableSet.aggregate` for the user-facing API.
        """
        output = []

        # Process nested TableSet's
        if isinstance(list(self._tables.values())[0], TableSet):
            for key, tableset in self._tables.items():
                column_names, column_types, nested_output = tableset._aggregate(aggregations)

                for row in nested_output:
                    row.insert(0, key)

                    output.append(row)

            column_names.insert(0, self._key_name)
            column_types.insert(0, self._key_type)
        # Regular Tables
        else:
            column_names = [self._key_name]
            column_types = [self._key_type]

            for column_name, aggregation, new_column_name in aggregations:
                c = self._sample_table.columns[column_name]

                column_names.append(new_column_name)
                column_types.append(aggregation.get_aggregate_column_type(c))

            for name, table in self._tables.items():
                new_row = [name]

                for column_name, aggregation, new_column_name in aggregations:
                    c = table.columns[column_name]

                    new_row.append(c.aggregate(aggregation))

                output.append(new_row)

        return column_names, column_types, output

    def aggregate(self, aggregations=[]):
        """
        Aggregate data from the tables in this set by performing some
        set of column operations on the groups and coalescing the results into
        a new :class:`.Table`.

        :code:`aggregations` must be a list of tuples, where each has three
        parts: a :code:`column_name`, a :class:`.Aggregation` instance and a
        :code:`new_column_name`.

        :param aggregations: An list of triples in the format
            :code:`(column_name, aggregation, new_column_name)`.
        :returns: A new :class:`.Table`.
        """
        column_names, column_types, output = self._aggregate(aggregations)

        return self._sample_table._fork(output, zip(column_names, column_types))
