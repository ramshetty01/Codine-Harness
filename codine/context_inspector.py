"""Inspect the exact next-request projection without mutating the archive."""
from collections import Counter
import json
from .context import project_context, estimate_tokens, ContextLimitError
from .instructions import load_instructions


def inspect_context(agent):
    schemas = agent.registry.schemas()
    guidance, paths = load_instructions(agent.config.project)
    guidance = agent.redactor.text(guidance)
    window, output = agent.config.request_limits(agent.session.model)
    report = {'estimate_method': 'conservative UTF-8 byte estimate, not a provider tokenizer',
              'instructions': paths, 'project_instruction_characters': len(guidance),
              'archive_messages': len(agent.session.messages), 'summary': 'No semantic summary is stored.',
              'schema_count': len(schemas), 'schema_characters': len(json.dumps(schemas, ensure_ascii=False)),
              'local_context_limit': window, 'output_reserved': output,
              'character_limit': agent.config.context_chars}
    try:
        messages, start = project_context(agent.session, schemas, agent.config.context_chars, guidance,
                                         token_budget=window, output_tokens=output)
    except ContextLimitError as error:
        report.update(status='over_limit', explanation=str(error), estimated_remaining=None)
        return report
    estimated = estimate_tokens(messages, schemas)
    report.update(status='fits', active_start=start, omitted_messages=max(0, start - 1),
                  active_messages=len(messages), roles=dict(Counter(row['role'] for row in messages)),
                  estimated_input=estimated, estimated_remaining=window - output - estimated,
                  tool_result_characters=sum(len(row.get('content', '')) for row in messages if row['role'] == 'tool'),
                  active_message_characters=len(json.dumps(messages, ensure_ascii=False)))
    return report


def format_context(report):
    lines = [f"Context: {report['status']} · {report['estimate_method']}",
             f"Instructions: {', '.join(report['instructions']) or 'built-in only'}",
             f"Archive: {report['archive_messages']} messages; {report['summary']}",
             f"Tools: {report['schema_count']} schemas, {report['schema_characters']} characters",
             f"Budget: {report['local_context_limit']} estimated tokens; {report['output_reserved']} reserved for output",
             f"Independent character ceiling: {report['character_limit']}"]
    if report['status'] == 'fits':
        lines.extend([f"Active: {report['active_messages']} messages; {report['omitted_messages']} archived messages omitted",
                      f"Input estimate: {report['estimated_input']}; remaining estimate: {report['estimated_remaining']}",
                      f"Tool results: {report['tool_result_characters']} characters",
                      'The next user message is not included yet; it consumes additional space.'])
    else:
        lines.append(report['explanation'])
    return '\n'.join(lines)
