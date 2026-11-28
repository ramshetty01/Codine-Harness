# Offline navigation acceptance fixture

Task: find the module-level normalizer and the similarly named class method,
locate the consumer's aliased call, and distinguish a shadowed parameter reference.
Plain substring search mixes these cases. Definitions provide distinct qualified
locations; references deliberately report lexical candidates, so the caller must
read the import and local scope before deciding what a rename would affect.

Tests additionally create a broken function and require a line-addressed syntax
diagnostic. The JavaScript file demonstrates the explicitly textual fallback.
All fixture source is authored for Codine; no third-party code or execution needed.
