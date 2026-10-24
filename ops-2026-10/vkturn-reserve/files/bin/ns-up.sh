#!/bin/bash
# creates only: the namespace "vkturn", one veth pair 10.99.0.0/30 (host side vkt-h). No routes, rules or firewall of the host are changed.
set -e
ip netns add vkturn
ip netns exec vkturn ip link set lo up
ip link add vkt-h type veth peer name vkt-n
ip link set vkt-n netns vkturn
ip addr add 10.99.0.1/30 dev vkt-h
ip link set vkt-h up
ip netns exec vkturn ip addr add 10.99.0.2/30 dev vkt-n
ip netns exec vkturn ip link set vkt-n up
