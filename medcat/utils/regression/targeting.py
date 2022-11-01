
from enum import Enum
import logging
from typing import Dict, Iterator, List, Set, Any, Union

from pydantic import BaseModel

from medcat.cdb import CDB

from medcat.utils.regression.utils import loosely_match_enum


logger = logging.getLogger(__name__)


class TargetInfo:
    """The helper class to identify individual target info.
    The main reason for this class is to simplify type hints.

    Args:
        cui (str): The CUI of the target
        val (str): The name/value of the target
    """

    def __init__(self, cui: str, val: str) -> None:
        self.cui = cui
        self.val = val

    def __str__(self) -> str:
        return f'TI[{self.cui}:{self.val}]'

    def __repr__(self) -> str:
        return f'<{self}>'


class TranslationLayer:
    """The translation layer for translating:
    - CUIs to names
    - names to CUIs
    - type_ids to CUIs
    - CUIs to chil CUIs

    The idea is to decouple these translations from the CDB instance in case something changes there.

    Args:
        cui2names (Dict[str, Set[str]]): The map from CUI to names
        name2cuis (Dict[str, Set[str]]): The map from name to CUIs
        cui2type_ids (Dict[str, Set[str]]): The map from CUI to type_ids
        cui2children (Dict[str, Set[str]]): The map from CUI to child CUIs
    """

    def __init__(self, cui2names: Dict[str, Set[str]], name2cuis: Dict[str, Set[str]],
                 cui2type_ids: Dict[str, Set[str]], cui2children: Dict[str, Set[str]]) -> None:
        self.cui2names = cui2names
        self.name2cuis = name2cuis
        self.cui2type_ids = cui2type_ids
        self.type_id2cuis: Dict[str, Set[str]] = {}
        for cui, type_ids in self.cui2type_ids.items():
            for type_id in type_ids:
                if type_id not in self.type_id2cuis:
                    self.type_id2cuis[type_id] = set()
                self.type_id2cuis[type_id].add(cui)
        self.cui2children = cui2children
        for cui in cui2names:
            if cui not in cui2children:
                self.cui2children[cui] = set()

    def targets_for(self, cui: str) -> Iterator[TargetInfo]:
        for name in self.cui2names[cui]:
            yield TargetInfo(cui, name)

    def all_targets(self, all_cuis: Set[str], all_names: Set[str], all_types: Set[str]) -> Iterator[TargetInfo]:
        """Get a generator of all target information objects.
        This is the starting point for checking cases.

        Args:
            all_cuis (Set[str]): The set of all CUIs to be queried
            all_names (Set[str]): The set of all names to be queried
            all_types (Set[str]): The set of all type IDs to be queried

        Yields:
            Iterator[TargetInfo]: The iterator of the target info
        """
        for cui in all_cuis:
            if cui not in self.cui2names:
                logger.warn('CUI not found in translation layer: %s', cui)
                continue
            for name in self.cui2names[cui]:
                yield TargetInfo(cui, name)
        for name in all_names:
            if name not in self.name2cuis:
                logger.warn('Name not found in translation layer: %s', name)
                continue
            for cui in self.name2cuis:
                if cui in all_cuis:
                    continue  # this cui-name pair should already have been yielded above
                yield TargetInfo(cui, name)
        for type_id in all_types:
            if type_id not in self.type_id2cuis:
                logger.warn(
                    'Type ID not found in translation layer: %s', type_id)
                continue
            for cui in self.type_id2cuis[type_id]:
                if cui in all_cuis:
                    continue  # should have been yielded above
                if cui not in self.cui2names:
                    logger.warn(
                        'CUI not found in translation layer: %s', cui)
                    continue
                for name in self.cui2names[cui]:
                    if name in all_names:
                        continue  # should have been yielded above
                    yield TargetInfo(cui, name)

    @classmethod
    def from_CDB(cls, cdb: CDB) -> 'TranslationLayer':
        """Construct a TranslationLayer object from a context database (CDB).

        This translation layer will refer to the same dicts that the CDB refers to.
        While there is no obvious reason these should be modified, it's something to keep in mind.

        Args:
            cdb (CDB): The CDB

        Returns:
            TranslationLayer: The subsequent TranslationLayer
        """
        return TranslationLayer(cdb.cui2names, cdb.name2cuis, cdb.cui2type_ids, cdb.addl_info['pt2ch'])


class FilterStrategy(Enum):
    """Describes the filter strategy.
    I.e whether to match all or any
    of the filters specified.
    """
    ALL = 1
    """Specified that all filters must be satisfied"""
    ANY = 2
    """Specified that any of the filters must be satisfied"""

    @classmethod
    def match_str(cls, name: str) -> 'FilterStrategy':
        """Find a loose string match.

        Args:
            name (str): The name of the enum

        Returns:
            FilterStrategy: The matched FilterStrategy
        """
        return loosely_match_enum(FilterStrategy, name)


class FilterType(Enum):
    """The types of targets that can be specified
    """
    TYPE_ID = 1
    """Filters by specified type_ids"""
    CUI = 2
    """Filters by specified CUIs"""
    NAME = 3
    """Filters by specified names"""
    CUI_AND_CHILDREN = 4
    """Filter by CUI but also allow children, up to a specified distance"""

    @classmethod
    def match_str(cls, name: str) -> 'FilterType':
        """Case insensitive matching for FilterType

        Args:
            name (str): The naeme to be matched

        Returns:
            FilterType: The matched FilterType
        """
        return loosely_match_enum(FilterType, name)


class TypedFilter(BaseModel):
    """A filter with multiple values to filter against.
    """
    type: FilterType
    values: List[str]

    def get_applicable_targets(self, translation: TranslationLayer, in_gen: Iterator[TargetInfo]) -> Iterator[TargetInfo]:
        """Get all applicable targets for this filter

        Args:
            translation (TranslationLayer): The translation layer
            in_gen (Iterator[TargetInfo]): The input generator / iterator

        Yields:
            Iterator[TargetInfo]: The output generator
        """
        if self.type == FilterType.CUI or self.type == FilterType.CUI_AND_CHILDREN:
            for ti in in_gen:
                if ti.cui in self.values:
                    yield ti
        if self.type == FilterType.NAME:
            for ti in in_gen:
                if ti.val in self.values:
                    yield ti
        if self.type == FilterType.TYPE_ID:
            for ti in in_gen:
                if ti.cui in translation.cui2type_ids:
                    tids = translation.cui2type_ids[ti.cui]
                else:
                    tids = set()
                for tid in tids:
                    if tid in self.values:
                        yield ti

    @classmethod
    def one_from_input(cls, target_type: str, vals: Union[str, list, dict]) -> 'TypedFilter':
        """Get one typed filter from the input target type and values.
        The values can either a be a string for a single target,
        a list of strings for multiple targets, or
        a dict in some more complicated cases (i.e CUI_AND_CHILDREN).

        Args:
            target_type (str): The target type as string
            vals (Union[str, list, dict]): The values

        Raises:
            ValueError: If the values are malformed

        Returns:
            TypedFilter: The parsed filter
        """
        t_type: FilterType = FilterType.match_str(target_type)
        filt: TypedFilter
        if isinstance(vals, dict):
            if t_type != FilterType.CUI_AND_CHILDREN:
                # currently only applicable for CUI_AND_CHILDREN case
                raise ValueError(f'Misconfigured config for {target_type}, '
                                 'expected either a value or a list of values '
                                 'for this type of filter')
            depth = vals['depth']
            delegate = cls.one_from_input(target_type, vals['cui'])
            if t_type is FilterType.CUI_AND_CHILDREN:
                filt = CUIWithChildFilter(
                    type=t_type, delegate=delegate, depth=depth)
        else:
            if isinstance(vals, str):
                vals = [vals, ]
            filt = TypedFilter(type=t_type, values=vals)
        return filt

    def to_dict(self) -> dict:
        """Convert the TypedFilter to a dict to be serialised.

        Returns:
            dict: The dict representation
        """
        return {self.type.name: self.values}

    @staticmethod
    def list_to_dicts(filters: List['TypedFilter']) -> List[dict]:
        """Create a list of dicts from list of TypedFilters.

        Args:
            filters (List[TypedFilter]): The list of typed filters

        Returns:
            List[dict]: The list of dicts
        """
        return [filt.to_dict() for filt in filters]

    @staticmethod
    def list_to_dict(filters: List['TypedFilter']) -> dict:
        """Create a single dict from the list of TypedFilters.

        Args:
            filters (List[TypedFilter]): The list of typed filters

        Returns:
            dict: The dict
        """
        d = {}
        for filt_dict in TypedFilter.list_to_dicts(filters):
            d.update(filt_dict)
        return d

    @classmethod
    def from_dict(cls, input: Dict[str, Any]) -> List['TypedFilter']:
        """Construct a list of TypedFilter from a dict.

        The assumed structure is:
        {<filter type>: <filtered value>}
        or
        {<filter type>: [<filtered value2>, <filtered value 2>]}
        There can be multiple filter types defined.

        Returns:
            List[TypedFilter]: The list of constructed TypedFilter
        """
        parsed_targets: List[TypedFilter] = []
        for target_type, vals in input.items():
            filt = cls.one_from_input(target_type, vals)
            parsed_targets.append(filt)
        return parsed_targets


class FilterOptions(BaseModel):
    """A class describing the options for the filters
    """
    strategy: FilterStrategy
    onlyprefnames: bool = False

    def to_dict(self) -> dict:
        """Convert the FilterOptions to a dict.

        Returns:
            dict: The dict representation
        """
        return {'strategy': self.strategy.name, 'prefname-only': str(self.onlyprefnames)}

    @classmethod
    def from_dict(cls, section: Dict[str, str]) -> 'FilterOptions':
        """Construct a FilterOptions instance from a dict.

        The assumed structure is:
        {'strategy': <'all' or 'any'>,
        'prefname-only': 'true'}

        Both strategy and prefname-only are optional.

        Args:
            section (Dict[str, str]): The dict to parse

        Returns:
            FilterOptions: The resulting FilterOptions
        """
        if 'strategy' in section:
            strategy = FilterStrategy.match_str(section['strategy'])
        else:
            strategy = FilterStrategy.ALL  # default
        if 'prefname-only' in section:
            onlyprefnames = section['prefname-only'].lower() == 'true'
        else:
            onlyprefnames = False
        return FilterOptions(strategy=strategy, onlyprefnames=onlyprefnames)


class CUIWithChildFilter(TypedFilter):
    delegate: TypedFilter
    depth: int
    values: List[str] = []  # overwrite TypedFilter

    def get_applicable_targets(self, translation: TranslationLayer, in_gen: Iterator[TargetInfo]) -> Iterator[TargetInfo]:
        """Get all applicable targets for this filter

        Args:
            translation (TranslationLayer): The translation layer
            in_gen (Iterator[TargetInfo]): The input generator / iterator

        Yields:
            Iterator[TargetInfo]: The output generator
        """
        for ti in self.delegate.get_applicable_targets(translation, in_gen):
            yield ti
            yield from self.get_children_of(translation, ti.cui, cur_depth=1)

    def get_children_of(self, translation: TranslationLayer, cui: str, cur_depth: int) -> Iterator[TargetInfo]:
        for child in translation.cui2children[cui]:
            yield from translation.targets_for(child)
            if cur_depth < self.depth:
                yield from self.get_children_of(translation, child, cur_depth=cur_depth + 1)

    def to_dict(self) -> dict:
        """Convert this CUIWithChildFilter to a dict.

        Returns:
            dict: The dict representation
        """
        return {self.type.name: {'depth': self.depth, 'cui': self.delegate.values}}
