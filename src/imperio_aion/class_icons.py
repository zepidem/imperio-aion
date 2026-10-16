# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Original 24-unit emblems inspired by class equipment, drawn as native vectors."""
from pathlib import Path

# Distinct silhouettes remain readable in the compact HUD.
SHAPES={
 'gladiator':[('polygon',[12,2,15,7,13,17,11,17,9,7]),('line',[5,15,19,15]),('line',[12,17,12,22]),('line',[8,22,16,22])],
 'templar':[('polygon',[12,2,20,5,19,14,12,22,5,14,4,5]),('line',[12,6,12,17]),('line',[8,10,16,10])],
 'assassin':[('polygon',[3,3,9,5,17,17,15,19,5,9]),('polygon',[21,3,15,5,7,17,9,19,19,9]),('line',[3,17,7,21]),('line',[17,21,21,17])],
 'ranger':[('line',[6,2,4,5,3,12,4,19,6,22]),('line',[6,2,11,12,6,22]),('line',[7,12,21,12]),('line',[17,8,21,12,17,16])],
 'sorcerer':[('polygon',[12,2,17,8,14,13,18,17,12,22,6,17,10,13,7,8]),('line',[12,7,12,17]),('line',[3,6,5,8]),('line',[19,6,21,8])],
 'spiritmaster':[('polygon',[12,2,22,12,12,22,2,12]),('oval',[7,7,17,17]),('line',[12,4,12,20]),('line',[4,12,20,12])],
 'cleric':[('line',[5,3,7,16,12,21,17,16,19,3]),('line',[5,6,19,6]),('line',[9,6,9,17]),('line',[12,6,12,19]),('line',[15,6,15,17]),('oval',[3,2,7,6]),('oval',[17,2,21,6])],
 'chanter':[('line',[12,3,12,22]),('polygon',[12,2,17,7,12,12,7,7]),('line',[3,10,4,17,12,21,20,17,21,10]),('line',[6,13,12,16,18,13])],
 'unknown':[('polygon',[12,3,21,12,12,21,3,12]),('oval',[10,10,14,14])]
}


def draw_icon(canvas,key,x,y,size,color):
    for kind,points in SHAPES.get(key,SHAPES['unknown']):
        coords=[(x if i%2==0 else y)+v*size/24 for i,v in enumerate(points)]
        if kind=='line':canvas.create_line(*coords,fill=color,width=max(1,size/16),capstyle='round',joinstyle='round')
        elif kind=='oval':canvas.create_oval(*coords,outline=color,width=max(1,size/16))
        else:canvas.create_polygon(*coords,fill='',outline=color,width=max(1,size/16),joinstyle='round')


def export_svg(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    for key,shapes in SHAPES.items():
        pieces=[]
        for kind,p in shapes:
            if kind in ('line','polygon'):
                pairs=' '.join(f'{p[i]},{p[i+1]}' for i in range(0,len(p),2))
                pieces.append(f'<{"polyline" if kind=="line" else "polygon"} points="{pairs}"/>')
            else:
                pieces.append(f'<ellipse cx="{(p[0]+p[2])/2}" cy="{(p[1]+p[3])/2}" rx="{(p[2]-p[0])/2}" ry="{(p[3]-p[1])/2}"/>')
        (folder/(key+'.svg')).write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#D5A65F" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'+''.join(pieces)+'</svg>',encoding='utf-8')
