"""The wooden case and movable paper scenery of Paper Theatre, in stage coordinates."""
import math
import random
from bisect import bisect_right

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

from .. import theme
from ..scape import smooth

WIDTH, HEIGHT = 1000, 450
BRASS = '#dfb77b'
IVORY = '#f6dfbb'
INK = '#25161a'
STAGE = QRectF(94, 53, 812, 344)
FLOOR = 397
CUE_NAMES = ('I', 'II', 'III', 'IV')
# Height of the town and bridge, travel of the bridge, and beam position.
BLOCKING = ((.83, .32, 82, .32), (1., .62, 36, .48),
            (.71, 1., 0, .66), (.91, .73, 27, .50))


def ease(value):
    value = max(0., min(1., value))
    return value * value * (3 - 2 * value)


class CueSheet:
    """Place scene changes near the strongest shifts in the measured envelope."""

    def __init__(self, wave=()):
        self.levels = smooth([v / 255 for v in wave], 7)
        n = len(self.levels)
        cues = [0.]
        for centre in (.25, .50, .75):
            cue = centre
            if n >= 30:
                span = max(2, int(n * .028))
                start, stop = int(n * (centre - .083)), int(n * (centre + .083))

                def strength(i):
                    before = sum(self.levels[i - span:i]) / span
                    after = sum(self.levels[i:i + span]) / span
                    return abs(after - before) - .36 * abs(i / n - centre)

                strongest = max(range(start, stop + 1), key=strength)
                if strength(strongest) > .042:
                    cue = strongest / n
            cues.append(cue)
        self.cues = tuple(cues)

    def act(self, progress):
        return bisect_right(self.cues, progress) - 1

    def blocking(self, progress):
        # The change begins at the cue and completes over a short part of the song.
        act = self.act(progress)
        if not act:
            return BLOCKING[0]
        t = ease((progress - self.cues[act]) / .027)
        return tuple(a + t * (b - a) for a, b in zip(BLOCKING[act - 1], BLOCKING[act]))

    def level(self, progress):
        if not self.levels:
            return .36
        i = progress * (len(self.levels) - 1)
        a = int(i)
        return self.levels[a] + (i - a) * (self.levels[min(a + 1, len(self.levels) - 1)] - self.levels[a])


def alpha(color, opacity):
    color = QColor(color)
    color.setAlphaF(opacity)
    return color


def gradient(y1, y2, *stops):
    g = QLinearGradient(0, y1, 0, y2)
    for pos, col in stops:
        g.setColorAt(pos, QColor(col))
    return g


def round_box(rect, radius):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def polygon(points):
    path = QPainterPath(QPointF(*points[0]))
    for xy in points[1:]:
        path.lineTo(QPointF(*xy))
    path.closeSubpath()
    return path


def arch(x, y, w, h):
    p = QPainterPath(QPointF(x, y + h))
    p.lineTo(x, y + w / 2)
    p.arcTo(x, y, w, w, 180, -180)
    p.lineTo(x + w, y + h)
    p.closeSubpath()
    return p


def fibre(p, rect, strength=.6):
    p.save()
    p.setOpacity(strength)
    p.drawTiledPixmap(rect, theme.grain(), QPointF(rect.x() % 128, rect.y() % 128))
    p.restore()


def paper(p, shape, pigment, depth=3):
    """Cut edge, contact shadow and printed face of a single card shape."""
    p.fillPath(shape.translated(depth * 1.25, depth * 1.7), QColor(15, 8, 15, 95))
    p.fillPath(shape.translated(0, depth), QColor(pigment).darker(145))
    r = shape.boundingRect()
    p.fillPath(shape, gradient(r.top(), r.bottom(), (0, QColor(pigment).lighter(111)), (1, pigment)))
    p.save()
    p.setClipPath(shape, Qt.ClipOperation.IntersectClip)
    fibre(p, r, .9)
    p.restore()


def plate(p, art, colour):
    """An album print mounted in the cyclorama, scored into three backlit folds."""
    field = theme.mix(colour, '#483653', .62)
    p.fillRect(STAGE, gradient(53, 397, (0, '#191728'), (.47, field), (1, '#bc8275')))
    randomizer = random.Random(19)
    p.setPen(Qt.PenStyle.NoPen)
    for _ in range(85):
        x, y = randomizer.uniform(112, 890), randomizer.uniform(78, 310)
        r = randomizer.choice((.55, .8, 1.35))
        p.setBrush(alpha(IVORY, randomizer.uniform(.15, .64)))
        p.drawEllipse(QPointF(x, y), r, r)

    # The visible print is close to square so small cover details and faces survive the set dressing.
    print_rect = QRectF(338, 77, 324, 314)
    shape = round_box(print_rect, 13)
    paper(p, round_box(print_rect.adjusted(-7, -7, 7, 7), 16), '#bc9779', 5)
    p.save()
    p.setClipPath(shape)
    if art is None or art.isNull():
        p.fillPath(shape, gradient(80, 390, (0, '#343149'), (.6, field), (1, '#eab893')))
        p.setBrush(theme.mix(colour, IVORY, .73))
        p.drawEllipse(QRectF(427, 121, 146, 146))
        for i in range(4):
            y = 241 + i * 34
            shade = theme.mix(field, '#ce8d7a', .47 - i * .11)
            p.fillPath(polygon([(320, y + 32), (406, y), (473, y + 35), (526, y + 13),
                                (565, y + 19), (686, y - 5), (686, 400), (320, 400)]), shade)
    else:
        side = min(art.width(), art.height())
        crop = QRectF((art.width() - side) / 2, (art.height() - side) / 2, side, side)
        p.drawImage(print_rect, art, crop)
        p.fillRect(print_rect, alpha('#9a5844', .13))
    for x in (446, 554):
        crease = QLinearGradient(x - 5, 0, x + 5, 0)
        crease.setColorAt(0, alpha(INK, 0))
        crease.setColorAt(.49, alpha(INK, .26))
        crease.setColorAt(.53, alpha(IVORY, .28))
        crease.setColorAt(1, alpha(IVORY, 0))
        p.fillRect(QRectF(x - 5, 77, 10, 314), crease)
    fibre(p, print_rect, 1)
    p.restore()


def town(p, colour):
    """Townhouses cut from the left wing. Windows are holes in the card."""
    houses = ((113, 186, 76, 32), (182, 246, 59, 29), (236, 199, 69, 21),
              (299, 268, 62, 27), (354, 294, 63, 16))
    for i, (x, y, w, roof) in enumerate(houses):
        p.fillRect(QRectF(x + 7, y + 12, w - 13, FLOOR - y - 16), QColor('#c08464'))
        body = polygon([(x, FLOOR), (x, y + 2), (x - 5, y + 2), (x + w * .51, y - roof),
                        (x + w * .73, y - roof * .55), (x + w * .73, y - roof - 5),
                        (x + w * .82, y - roof - 5), (x + w * .82, y - roof * .42),
                        (x + w + 5, y + 2), (x + w, y + 2), (x + w, FLOOR)])
        windows = []
        for row, yy in enumerate(range(y + 19, FLOOR - 31, 39)):
            for xx in (x + w * .21, x + w * .61):
                hole = arch(xx, yy, w * .14, 20)
                body = body.subtracted(hole)
                windows.append((xx, yy, row))
        pigment = theme.mix('#6a3b55' if i % 2 == 0 else '#995b62', colour, .19)
        paper(p, body, pigment)
        p.setPen(QPen(alpha(IVORY, .32), 1.2))
        for xx, yy, row in windows:
            p.drawLine(QPointF(xx, yy + 10), QPointF(xx + w * .14, yy + 10))
            p.drawLine(QPointF(xx + w * .07, yy + 3), QPointF(xx + w * .07, yy + 20))
            if row == 0:
                p.drawLine(QPointF(xx - 3, yy + 23), QPointF(xx + w * .14 + 3, yy + 23))
        p.setPen(QPen(alpha(IVORY, .27), 1))
        p.drawLine(QPointF(x + 7, y + 5), QPointF(x + w - 5, y + 5))


def bridge(p, colour):
    # Repeated knife cuts reveal the backdrop through arches as the bridge opens.
    paper(p, polygon([(811, 394), (811, 192), (836, 192), (836, 165), (849, 165),
                      (849, 140), (861, 123), (877, 142), (877, 186), (905, 186),
                      (905, 397)]).subtracted(arch(854, 203, 22, 66)), '#825070')
    deck = polygon([(568, 397), (568, 333), (644, 310), (779, 272), (907, 257), (907, 397)])
    for x, y, w in ((588, 341, 61), (667, 315, 68), (753, 292, 78), (850, 280, 48)):
        deck = deck.subtracted(arch(x, y, w, FLOOR - y + 14))
    paper(p, deck, theme.mix('#ce8c76', colour, .18), 4)
    p.setPen(QPen(alpha(IVORY, .65), 2))
    for path_y in (0, 8):
        line = QPainterPath(QPointF(572, 333 + path_y))
        line.quadTo(782, 259 + path_y, 906, 257 + path_y)
        p.drawPath(line)
    for x in range(591, 902, 16):
        t = (x - 572) / 334
        y = 333 - 76 * (1 - (1 - t) ** 1.5)
        p.drawLine(QPointF(x, y - 13), QPointF(x, y))


def bird(p, colour):
    """A swallow hung on real rigging line, drawn in folded slivers of ink."""
    p.setPen(QPen(alpha(IVORY, .32), 1))
    p.drawLine(QPointF(4, -245), QPointF(4, -3))
    shape = polygon([(-36, -24), (-15, -16), (-4, -4), (8, -7), (32, -26), (44, -25),
                     (24, -9), (21, 0), (35, 4), (18, 9), (6, 8), (-9, 15), (-25, 10),
                     (-43, 8), (-23, 1), (-25, -7)])
    paper(p, shape, theme.mix(IVORY, colour, .28), 1.5)
    p.fillPath(polygon([(-36, -24), (-4, -4), (-10, 4), (-25, -7)]), alpha('#a97070', .58))
    p.fillPath(polygon([(8, -7), (44, -25), (21, 0), (-9, 15)]), alpha('#a97070', .48))


def curtain(p, closed=False):
    length = 415 if closed else 112
    for mirror in (False, True):
        p.save()
        if mirror:
            p.translate(WIDTH, 0)
            p.scale(-1, 1)
        x = 93 + length
        drape = QPainterPath(QPointF(84, 43))
        drape.lineTo(x, 43)
        drape.cubicTo(x - 7, 123, x - 21, 179, x - 24, 240)
        drape.cubicTo(x - 22, 309, x - 3, 362, x + 3, 396)
        drape.quadTo(x - 68, 418, 84, 393)
        drape.closeSubpath()
        paper(p, drape, '#6b293e', 5)
        p.save()
        p.setClipPath(drape, Qt.ClipOperation.IntersectClip)
        for i in range(1, 15 if closed else 6):
            a = 85 + i * (29 if closed else 19)
            g = QLinearGradient(a - 10, 0, a + 10, 0)
            g.setColorAt(0, alpha('#efaa89', 0))
            g.setColorAt(.43, alpha('#efaa89', .23))
            g.setColorAt(.67, alpha(INK, .42))
            g.setColorAt(1, alpha(INK, 0))
            p.fillRect(QRectF(a - 10, 43, 20, 355), g)
        p.restore()
        p.setPen(QPen(alpha(BRASS, .8), 2.2))
        p.drawPath(drape)
        if not closed:
            p.drawArc(QRectF(78, 191, 122, 56), 177 * 16, 175 * 16)
            p.setPen(QPen(alpha(BRASS, .85), 1.3))
            for i in range(6):
                p.drawLine(QPointF(173 + i, 230), QPointF(170 + i * 2, 251))
        p.restore()


def surround(p):
    """The case, gilded proscenium and the row of real footlights."""
    shell = round_box(QRectF(14, 8, 972, 436), 30)
    hole = round_box(STAGE, 19)
    paper(p, shell.subtracted(hole), '#502d36', 6)
    p.strokePath(round_box(QRectF(20, 13, 960, 423), 26), QPen(alpha(BRASS, .51), 1.5))
    p.strokePath(round_box(QRectF(28, 21, 944, 409), 21), QPen(alpha('#1d1016', .8), 4))
    inner = round_box(QRectF(64, 37, 872, 375), 28).subtracted(hole)
    paper(p, inner, '#cb9d6a', 3)
    p.strokePath(round_box(QRectF(69, 41, 862, 368), 25), QPen(QColor('#f9d8a4'), 1.5))
    p.strokePath(hole, QPen(QColor('#472b32'), 4))

    for x in (42, 958):
        for y in (36, 410):
            screw(p, x, y, 3.6)
    for x in (44, 956):
        p.setPen(QPen(alpha(BRASS, .52), 1.3))
        p.drawLine(QPointF(x, 69), QPointF(x, 375))
        for y in (62, 382):
            p.fillPath(polygon([(x, y - 4), (x + 3, y), (x, y + 4), (x - 3, y)]), QColor(BRASS))

    # Fan engraving and rivets on the crown.
    p.setPen(QPen(alpha(BRASS, .72), 1.1))
    for k in range(-5, 6):
        p.drawLine(QPointF(500, 34), QPointF(500 + 4 * k, 19 + abs(k) * 1.7))
    for s in (-1, 1):
        curl = QPainterPath(QPointF(500 + s * 25, 31))
        curl.cubicTo(500 + s * 49, 13, 500 + s * 57, 40, 500 + s * 83, 28)
        p.drawPath(curl)
        p.fillPath(polygon([(500 + s * 93, 22), (500 + s * 99, 28),
                            (500 + s * 93, 34), (500 + s * 87, 28)]), QColor(BRASS))

    apron = QRectF(74, 397, 852, 31)
    p.fillRect(apron, gradient(397, 428, (0, '#c18d62'), (.16, '#77454b'), (1, '#371f27')))
    p.fillRect(QRectF(74, 398, 852, 1.6), QColor('#f4c591'))
    p.fillRect(QRectF(74, 426, 852, 1), QColor('#8d5c51'))
    for x in range(130, 876, 46):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#482830'))
        p.drawEllipse(QPointF(x, 405), 7.1, 4.3)
        p.setBrush(QColor('#f2c583'))
        p.drawEllipse(QPointF(x, 403.8), 4.4, 2.6)


def screw(p, x, y, radius):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor('#583d31'))
    p.drawEllipse(QPointF(x, y + .7), radius + .7, radius + .7)
    p.setBrush(QColor(BRASS))
    p.drawEllipse(QPointF(x, y), radius, radius)
    p.setPen(QPen(QColor('#6d4834'), .9))
    p.drawLine(QPointF(x - radius * .55, y + 1), QPointF(x + radius * .55, y - 1))


def layer(rect, draw, ratio):
    px = QPixmap(math.ceil(rect.width() * ratio), math.ceil(rect.height() * ratio))
    px.setDevicePixelRatio(ratio)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    p.translate(-rect.x(), -rect.y())
    draw(p)
    p.end()
    return px


class TheatreSet:
    """Render the heavy card layers once. The playhead only moves up to three cutouts and a light."""
    def __init__(self, colour, art=None, closed=False, ratio=1.):
        self.colour, self.closed = QColor(colour), closed
        self.opening = round_box(STAGE, 19)
        self.back = layer(STAGE, lambda p: plate(p, art, colour), ratio) if not closed else None
        self.town_box = QRectF(103, 139, 327, 269)
        self.bridge_box = QRectF(551, 111, 364, 297)
        if not closed:
            self.town = layer(self.town_box, lambda p: town(p, colour), ratio)
            self.bridge = layer(self.bridge_box, lambda p: bridge(p, colour), ratio)
            self.bird = layer(QRectF(-47, -246, 97, 268), lambda p: bird(p, colour), ratio)

        def frame(p):
            p.save()
            p.setClipPath(self.opening)
            curtain(p, closed)
            p.restore()
            surround(p)

        self.frame = layer(QRectF(0, 0, WIDTH, HEIGHT), frame, ratio)

    def draw(self, p, rect, sheet, progress):
        p.save()
        p.translate(rect.x(), rect.y())
        p.scale(rect.width() / WIDTH, rect.height() / HEIGHT)
        if not self.closed:
            town_h, bridge_h, shift, light_x = sheet.blocking(progress)
            level = sheet.level(progress)
            p.save()
            p.setClipPath(self.opening)
            p.drawPixmap(STAGE.topLeft(), self.back)
            stage_light = QRadialGradient(QPointF(140 + light_x * 720, 368), 293)
            stage_light.setColorAt(0, alpha('#ffbc84', .27 + level * .20))
            stage_light.setColorAt(1, alpha(IVORY, 0))
            p.fillRect(STAGE, stage_light)
            # The rig carries the swallow across the stage even between scenery cues.
            fly = progress * math.pi
            bird_x = 235 + 532 * progress
            bird_y = 176 - 54 * math.sin(fly) + 6 * math.sin(6 * fly)
            p.drawPixmap(QPointF(bird_x - 47, bird_y - 246), self.bird)
            self._wing(p, self.town, self.town_box, town_h, 0)
            self._wing(p, self.bridge, self.bridge_box, bridge_h, shift)
            p.fillRect(QRectF(94, 350, 812, 47), gradient(350, 397, (0, alpha(INK, 0)), (1, alpha(INK, .51))))
            p.restore()
        p.drawPixmap(QPointF(0, 0), self.frame)
        p.restore()

    @staticmethod
    def _wing(p, px, box, height, shift):
        p.save()
        p.translate(shift, FLOOR)
        p.scale(1, height)
        p.drawPixmap(QPointF(box.x(), box.y() - FLOOR), px)
        p.restore()
