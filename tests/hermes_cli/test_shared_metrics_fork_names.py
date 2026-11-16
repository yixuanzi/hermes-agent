"""The Aegis fork's tools and toolsets never reach shared metrics under their own names."""

from hermes_cli.observability import shared_metrics_contract as contract
from hermes_cli.observability.shared_metrics_efficiency import toolset_metric_name


def test_fork_toolsets_record_as_custom_and_their_tools_as_plugin():
    for toolset in ("aegis_a2a", "userenv"):
        assert toolset_metric_name(toolset) == "custom"
    for tool in ("aegis_a2a_delegate", "aegis_a2a_list", "userenv"):
        assert contract.tool_metric_name({"tool_name": tool}) == "plugin"
