"""python -m scrollweave stitch/compose ..."""

import argparse
import sys

from . import (
    AlignOptions,
    Image,
    LayeredOptions,
    PlanOptions,
    ScrollWeaveError,
    Segmentation,
    Viewport,
    __version__,
    compose,
    plan_views,
    stitch_basic,
    stitch_layered,
)


def _main(argv=None):
    parser = argparse.ArgumentParser(prog='scrollweave')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    sub = parser.add_subparsers(dest='operation', required=True)

    st = sub.add_parser('stitch', help='reconstruct scrolling screenshots')
    st.add_argument('images', nargs='+')
    st.add_argument('-o', '--output', required=True)
    st.add_argument('--algorithm', choices=('layered', 'basic'), default='layered')
    st.add_argument('--positions', help='comma-separated scroll positions; omit to estimate')
    st.add_argument('--viewport', help='top:bottom content band')
    st.add_argument('--no-trim', action='store_true')
    st.add_argument('--no-appearance', action='store_true')
    st.add_argument('--segmentation', choices=('temporal', 'graphcut'), default='temporal')
    st.add_argument('--debug-dir')
    st.add_argument('--recommend-views', type=int, default=0)
    st.add_argument('--recommend-repair-views', type=int, default=0)
    st.add_argument('--candidate-positions', help='comma-separated candidate scroll offsets')
    st.add_argument('--minimum-separation', type=int, default=48)

    cp = sub.add_parser('compose', help='compose without alignment')
    cp.add_argument('images', nargs='+')
    cp.add_argument('-o', '--output', required=True)
    cp.add_argument(
        '--layout', choices=['vertical', 'horizontal', 'grid', 'overlay'], default='vertical'
    )
    cp.add_argument('--columns', type=int, default=2)
    cp.add_argument('--gap', type=int, default=0)

    args = parser.parse_args(argv)
    if args.operation == 'compose':
        output = compose(args.images, layout=args.layout, columns=args.columns, gap=args.gap)
        output.save(args.output)
        print(f'Saved {args.output}: {output.width}x{output.height}')
        return

    frames = [Image.open(path) for path in args.images]
    viewport = None
    if args.viewport is not None:
        top, bottom = (int(value) for value in args.viewport.split(':'))
        viewport = Viewport(top, bottom)
    positions = [int(value) for value in args.positions.split(',')] if args.positions else None

    if args.algorithm == 'basic':
        result = stitch_basic(
            frames,
            positions=positions,
            viewport=viewport,
            options=AlignOptions(auto_trim=not args.no_trim),
            diagnostics=True,
        )
        placed = len([position for position in result.positions if position is not None])
    else:
        if viewport is None and args.no_trim:
            viewport = Viewport(0, frames[0].height)
        options = LayeredOptions(
            appearance=not args.no_appearance,
            segmentation=Segmentation(args.segmentation),
        )
        result = stitch_layered(
            frames,
            scroll=positions,
            viewport=viewport,
            options=options,
            diagnostics=True,
        )
        placed = len(result.scroll)

    result.save(args.output)
    if args.debug_dir:
        result.save_debug(args.debug_dir)
    for count, goal in (
        (args.recommend_views, 'verification'),
        (args.recommend_repair_views, 'repair'),
    ):
        if count == 0:
            continue
        if not hasattr(result, 'scroll'):
            raise SystemExit('planning requires the layered pipeline')
        planners = plan_views(
            result,
            PlanOptions(
                count=count,
                goal=goal,
                candidates=(
                    [int(value) for value in args.candidate_positions.split(',')]
                    if args.candidate_positions
                    else None
                ),
                minimum_separation=args.minimum_separation,
            ),
        )
        for view in planners:
            print(
                f'Recommend {view.goal.value}: scroll={view.scroll_position}, '
                f'estimated_gain={view.estimated_gain:.1f}'
            )
    print(
        f'Saved {args.output}: {result.image.width}x{result.image.height}, '
        f'{placed}/{len(frames)} frames placed'
    )


def main(argv=None):
    try:
        return _main(argv)
    except ScrollWeaveError as error:
        print(f'scrollweave: {error}', file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
