"""Core controls for bounded integration schema discovery, never server loading."""
from .registry import ToolSpec


def register_discovery_tools(registry):
    string = {'type':'string'}
    def add(name, description, handler, properties, required=()):
        registry.register(ToolSpec(name,description,{'type':'object','properties':properties,
            'required':list(required),'additionalProperties':False},handler,effect='read'))
    add('discover_tools','Search bounded metadata for already connected integration tools. No schema or server is loaded; permissions remain unchanged.',
        registry.catalog,{'query':string,'limit':{'type':'integer','minimum':1,'maximum':20}})
    add('load_tool','Schedule one exact discovered integration schema for future model requests after this complete tool batch is saved. Cannot invoke it in the same batch. No trust or execution permissions granted.',
        registry.request_load,{'name':string,'version':string},('name','version'))
    add('unload_tools','Remove integration schemas from future requests at the next complete tool boundary; does not disconnect servers.',
        registry.request_unload,{'names':{'type':'array','items':string}},('names',))
