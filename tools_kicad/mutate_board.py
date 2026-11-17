"""In-container board mutation for controlled defects: delete the first track segment of a named net.
Usage: python3 mutate_board.py BOARD NET_NAME OUT_BOARD"""
import sys

import pcbnew

board = pcbnew.LoadBoard(sys.argv[1])
net = sys.argv[2]
victims = [t for t in board.GetTracks() if t.GetNetname() == net and t.GetClass() == "PCB_TRACK"]
if not victims:
    sys.exit(f"no track on net {net}")
victim = victims[0]
print(f"deleting track on {net} at {pcbnew.ToMM(victim.GetStart().x):.2f},{pcbnew.ToMM(victim.GetStart().y):.2f} -> {pcbnew.ToMM(victim.GetEnd().x):.2f},{pcbnew.ToMM(victim.GetEnd().y):.2f} on {board.GetLayerName(victim.GetLayer())}")
board.Remove(victim)
board.Save(sys.argv[3])
