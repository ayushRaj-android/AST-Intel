"""Language-specific AST extractors.

Each extractor implements :class:`ExtractorBase` and handles a single language
(or language family). The dispatcher routes files to the appropriate extractor
based on file extension.
"""
