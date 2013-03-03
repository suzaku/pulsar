'''Routing classes for managing urls. Originally from the routing
module in werkzeug_.

Original License

copyright (c) 2011 by the Werkzeug Team. License BSD

.. _werkzeug: https://github.com/mitsuhiko/werkzeug

A :class:`Route` is a class for relative url paths::

    Route('bla')
    Route('bla/foo')

Integers::

    # accept any integer
    Route('<int:size>')
    # accept an integer between 1 and 200 only
    Route('<int(min=1,max=200):size>')

Route decorator
==================

.. autoclass:: route
   :members:
   :member-order: bysource
   
   
Route
================

.. autoclass:: Route
   :members:
   :member-order: bysource
   
'''
import re

from pulsar import Http404, HttpException
from pulsar.utils.httpurl import iteritems, iri_to_uri, remove_double_slash
from pulsar.utils.httpurl import ENCODE_URL_METHODS, ENCODE_BODY_METHODS
from pulsar.utils.pep import to_string


__all__ = ['route', 'Route']


_rule_re = re.compile(r'''
    (?:
        (?P<converter>[a-zA-Z_][a-zA-Z0-9_]*)   # converter name
        (?:\((?P<args>.*?)\))?                  # converter arguments
        \:                                      # variable delimiter
    )?
    (?P<variable>[a-zA-Z_][a-zA-Z0-9_]*)        # variable name
''', re.VERBOSE)

_converter_args_re = re.compile(r'''
    ((?P<name>\w+)\s*=\s*)?
    (?P<value>
        True|False|
        \d+.\d+|
        \d+.|
        \d+|
        \w+|
        [urUR]?(?P<stringval>"[^"]*?"|'[^']*')
    )\s*,
''', re.VERBOSE|re.UNICODE)


_PYTHON_CONSTANTS = {
    'None':     None,
    'True':     True,
    'False':    False
}


def _pythonize(value):
    if value in _PYTHON_CONSTANTS:
        return _PYTHON_CONSTANTS[value]
    for convert in int, float:
        try:
            return convert(value)
        except ValueError:
            pass
    if value[:1] == value[-1:] and value[0] in '"\'':
        value = value[1:-1]
    return unicode(value)


def parse_rule(rule):
    """Parse a rule and return it as generator. Each iteration yields tuples
    in the form ``(converter, arguments, variable)``. If the converter is
    `None` it's a static url part, otherwise it's a dynamic one.

    :internal:
    """
    m = _rule_re.match(rule)
    if m is None or m.end() < len(rule):
        raise ValueError('Error while parsing rule {0}'.format(rule))
    data = m.groupdict()
    converter = data['converter'] or 'default'
    return converter, data['args'] or None, data['variable']


class route(object):
    '''Decorator for creating a (:class:`Route`, HTTP method, parameters) tuple
and assign it to the `rule_method` attribute of the functon which
is decorated. Check the :ref:`HttpBin example <httpbin-example>`
for a sample usage.

:param rule: Optional string for the relative url served by the method which
    is decorated. If not supplied, the method name is used.
:param method: Optional HTTP method name. Default is `get`.
:param rule: Optional dictionary of defaults parameter.
    Used when creating the :class:`Route`.
'''
    creation_count = 0
    def __init__(self, rule=None, method=None, defaults=None, **parameters):
        self.__class__.creation_count += 1
        self.creation_count = self.__class__.creation_count
        '''Create a new Router'''
        self.rule = rule
        self.defaults = defaults
        self.method = method
        self.parameters = parameters
        
    def __call__(self, func):
        '''func could be an unbound method of a Router class or a standard
python function.'''
        bits = func.__name__.split('_')
        method = None
        if len(bits) > 1:
            m = bits[0].upper()
            if m in ENCODE_URL_METHODS or method in ENCODE_BODY_METHODS:
                method = m
                bits = bits[1:]
        method = (self.method or method or 'get').lower()
        rule = Route(self.rule or '_'.join(bits), defaults=self.defaults)
        func.rule_method = (rule, method, self.parameters, self.creation_count)
        return func
        
    
class Route(object):
    '''A Route is a class with a relative :attr:`path`.
    
:parameter rule: Rule strings basically are just normal URL paths
    with placeholders in the format ``<converter(arguments):name>``
    where the converter and the arguments are optional.
    If no converter is defined the `default` converter is used which
    means `string`.
    
:parameter defaults: optional dictionary of default values for variables.
:parameter append_slash: Force a slash to be the last character of the rule.
    In doing so the :attr:`is_leaf` is guaranteed to be ``False``.
    
    
.. attribute:: is_leaf

    If ``True``, the route is equivalent to a file and no sub-routes can be
    added.
    
.. attribute:: path

    The full path for this route including initial ``'/'``.
    
.. attribute:: arguments

    a set of arguments for this route. If the route has no variables, the
    set is empty.
    
.. _werkzeug: https://github.com/mitsuhiko/werkzeug
'''
    def __init__(self, rule, defaults=None):
        rule = remove_double_slash('/%s' % rule)
        self.defaults = defaults if defaults is not None else {}
        self.is_leaf = not rule.endswith('/')
        self.rule = rule[1:]
        self.arguments = set(map(str, self.defaults))
        breadcrumbs = []
        self._converters = {}
        regex_parts = []
        if self.rule:
            for bit in self.rule.split('/'):
                if not bit:
                    continue
                s = bit[0]
                e = bit[-1]
                if s == '<' or e == '>':
                    if s + e != '<>':
                        raise ValueError('malformed rule {0}'.format(self.rule))
                    converter, arguments, variable = parse_rule(bit[1:-1])
                    if variable in self._converters:
                        raise ValueError('variable name {0} used twice\
 in rule {1}.'.format(variable,self.rule))
                    convobj = get_converter(converter, arguments)
                    regex_parts.append('(?P<%s>%s)' % (variable, convobj.regex))
                    breadcrumbs.append((True,variable))
                    self._converters[variable] = convobj
                    self.arguments.add(str(variable))
                else:
                    variable = bit
                    regex_parts.append(re.escape(variable))
                    breadcrumbs.append((False, variable))
                    
        self.breadcrumbs = tuple(breadcrumbs)
        self._regex_string = '/'.join(regex_parts)
        if self._regex_string and not self.is_leaf:
            self._regex_string += '/'
        self._regex = re.compile(self.regex, re.UNICODE)
    
    @property
    def level(self):
        return len(self.breadcrumbs)
        
    @property
    def path(self):
        return '/' + self.rule
    
    @property
    def regex(self):
        if self.is_leaf:
            return '^' + self._regex_string + '$'
        else:
            return '^' + self._regex_string
    
    @property
    def bits(self):
        return tuple((b[1] for b in self.breadcrumbs))
    
    def ordered_variables(self):
        return tuple((b for dyn,b in self.breadcrumbs if dyn))
    
    def __hash__(self):
        return hash(self.rule)
    
    def __repr__(self):
        return self.path
    
    def __eq__(self, other):
        if isinstance(other,self.__class__):
            return str(self) == str(other)
        else:
            return False
        
    def __lt__(self, other):
        if isinstance(other,self.__class__):
            return to_string(self) < to_string(other)
        else:
            raise TypeError('Cannot compare {0} with {1}'.format(self,other))

    def _url_generator(self, values):
        for is_dynamic, val in self.breadcrumbs:
            if is_dynamic:
                val = self._converters[val].to_url(values[val])
            yield val
            
    def url(self, **urlargs):
        '''Build a *url* from *urlargs* dictionary.'''
        if self.defaults:
            d = self.defaults.copy()
            d.update(urlargs)
            urlargs = d
        url = '/'.join(self._url_generator(urlargs))
        if not url:
            return '/'
        else:
            url = '/' + url
            return url if self.is_leaf else url + '/'
        
    def safe_url(self, params=None):
        try:
            if params:
                return self.url(**params)
            else:
                return self.url()
        except KeyError:
            return None
    
    def match(self, path):
        '''Match a path and return ``None`` if no matching, otherwise
 a dictionary of matched variables with values. If there is more
 to be match in the path, the remaining string is placed in the
 ``__remaining__`` key of the dictionary.'''
        match = self._regex.search(path)
        if match is not None:
            remaining = path[match.end():]
            groups = match.groupdict()
            result = {}
            for name, value in iteritems(groups):
                try:
                    value = self._converters[name].to_python(value)
                except Http404:
                    return
                result[str(name)] = value
            if remaining:
                result['__remaining__'] = remaining
            return result
        
    def split(self):
        '''Return a two element tuple containing the parent route and
the last url bit as route. If this route is the root route, it returns
the root route and ``None``. '''
        rule = self.rule
        if not self.is_leaf:
            rule = rule[:-1]
        if not rule:
            return Route('/'),None 
        bits = ('/'+rule).split('/')
        last = Route(bits[-1] if self.is_leaf else bits[-1] + '/')  
        if len(bits) > 1:
            return Route('/'.join(bits[:-1]) + '/'),last
        else:
            return last,None
        
    def __add__(self, other):
        if self.is_leaf:
            raise ValueError('Cannot prepend {0} to {1}. '\
                             'It is a leaf.'.format(self, other))
        cls = self.__class__
        defaults = self.defaults.copy()
        if isinstance(other,cls):
            rule = other.rule
            defaults.update(other.defaults)
        else:
            rule = str(other)
        return cls(self.rule + rule, defaults)
    
    def __deepcopy__(self, memo):
        return self.__copy__()
    
    def __copy__(self):
        return self.__class__(self.rule, self.defaults.copy())
        

class BaseConverter(object):
    """Base class for all converters."""
    regex = '[^/]+'
    weight = 100

    def to_python(self, value):
        return value

    def to_url(self, value):
        return iri_to_uri(value)


class StringConverter(BaseConverter):
    """This converter is the default converter and accepts any string but
    only one path segment.  Thus the string can not include a slash.

    This is the default validator.

    Example::

        Rule('/pages/<page>'),
        Rule('/<string(length=2):lang_code>')

    :param minlength: the minimum length of the string.  Must be greater
                      or equal 1.
    :param maxlength: the maximum length of the string.
    :param length: the exact length of the string.
    """

    def __init__(self, minlength=1, maxlength=None, length=None):
        if length is not None:
            length = '{%d}' % int(length)
        else:
            if maxlength is None:
                maxlength = ''
            else:
                maxlength = int(maxlength)
            length = '{%s,%s}' % (
                int(minlength),
                maxlength
            )
        self.regex = '[^/]' + length


class AnyConverter(BaseConverter):
    """Matches one of the items provided.  Items can either be Python
    identifiers or strings::

        Rule('/<any(about, help, imprint, class, "foo,bar"):page_name>')

    :param items: this function accepts the possible items as positional
                  arguments.
    """

    def __init__(self, *items):
        self.regex = '(?:%s)' % '|'.join([re.escape(x) for x in items])


class PathConverter(BaseConverter):
    """Like the default :class:`StringConverter`, but it also matches
    slashes.  This is useful for wikis and similar applications::

        Rule('/<path:wikipage>')
        Rule('/<path:wikipage>/edit')
    """
    regex = '[^/].*?'
    regex = '.*'
    weight = 200


class NumberConverter(BaseConverter):
    """Baseclass for `IntegerConverter` and `FloatConverter`.

    :internal:
    """
    weight = 1

    def __init__(self, fixed_digits=0, min=None, max=None):
        self.fixed_digits = fixed_digits
        self.min = min
        self.max = max

    def to_python(self, value):
        if (self.fixed_digits and len(value) != self.fixed_digits):
            raise Http404()
        value = self.num_convert(value)
        if (self.min is not None and value < self.min) or \
           (self.max is not None and value > self.max):
            raise Http404()
        return value

    def to_url(self, value):
        if (self.fixed_digits and len(str(value)) > self.fixed_digits):
            raise ValueError()
        value = self.num_convert(value)
        if (self.min is not None and value < self.min) or \
           (self.max is not None and value > self.max):
            raise ValueError()
        if self.fixed_digits: 
            value = ('%%0%sd' % self.fixed_digits) % value
        return str(value)


class IntegerConverter(NumberConverter):
    """This converter only accepts integer values::

        Rule('/page/<int:page>')

    This converter does not support negative values.

    :param fixed_digits: the number of fixed digits in the URL.  If you set
                         this to ``4`` for example, the application will
                         only match if the url looks like ``/0001/``.  The
                         default is variable length.
    :param min: the minimal value.
    :param max: the maximal value.
    """
    regex = r'\d+'
    num_convert = int


class FloatConverter(NumberConverter):
    """This converter only accepts floating point values::

        Rule('/probability/<float:probability>')

    This converter does not support negative values.

    :param min: the minimal value.
    :param max: the maximal value.
    """
    regex = r'\d+\.\d+'
    num_convert = float

    def __init__(self, min=None, max=None):
        super(FloatConverter,self).__init__(0, min, max)


def parse_converter_args(argstr):
    argstr += ','
    args = []
    kwargs = {}

    for item in _converter_args_re.finditer(argstr):
        value = item.group('stringval')
        if value is None:
            value = item.group('value')
        value = _pythonize(value)
        if not item.group('name'):
            args.append(value)
        else:
            name = item.group('name')
            kwargs[name] = value

    return tuple(args), kwargs


def get_converter(name, arguments):
    c = _CONVERTERS.get(name)
    if not c:
        raise LookupError('Route converter {0} not available'.format(name))
    if arguments:
        args, kwargs = parse_converter_args(arguments)
        return c(*args,**kwargs)
    else:
        return c()

    
#: the default converter mapping for the map.
_CONVERTERS = {
    'default':          StringConverter,
    'string':           StringConverter,
    'any':              AnyConverter,
    'path':             PathConverter,
    'int':              IntegerConverter,
    'float':            FloatConverter
}