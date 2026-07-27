from jtv.model import Agent, ServiceTask, build_line_network
from jtv.dp import single_agent_dp, joint_group_dp
from jtv.algorithms import conflict_keys


def test_single_and_joint_feasibility():
    net = build_line_network(5)
    agents = [
        Agent(0, 0, 4, 0, 16, (ServiceTask(0, 1, 1),)),
        Agent(1, 4, 0, 0, 16, (ServiceTask(0, 3, 1),)),
    ]
    singles = [single_agent_dp(net, a) for a in agents]
    assert all(p is not None for p in singles)
    assert conflict_keys(singles)
    joint = joint_group_dp(net, agents)
    assert joint is not None
    assert not conflict_keys([joint])


def test_completion_progress():
    net = build_line_network(4)
    a = Agent(0, 0, 3, 0, 8, (ServiceTask(0, 1, 2),))
    p = single_agent_dp(net, a)
    assert p is not None
    assert p.states_by_agent[0][-1].completion.completed_prefix == 1
