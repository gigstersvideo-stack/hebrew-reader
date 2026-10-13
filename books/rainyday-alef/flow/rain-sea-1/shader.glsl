
precision highp float;
uniform vec2 u_res;
uniform float u_dpr, u_k, u_pad;
uniform float u_time, u_rain, u_hl, u_variant;
uniform float u_dissolve, u_drip, u_puddleR, u_morph, u_roomWater, u_open, u_fall, u_seaTop, u_kidsBoat, u_pageRain;
uniform float u_wind, u_flight, u_splashT, u_splashX, u_paintT, u_gust, u_hand;
uniform vec4 u_uri;             // Ури: сдвиг ладошки xy, надышанное пятно, отпечаток
uniform vec4 u_boat, u_fish;
uniform sampler2D u_tRoom, u_tKids, u_tSash, u_tExt, u_tBoat, u_tBoatKids, u_tFish;
uniform sampler2D u_tDrops;     // карта капель из симуляции (glass-sim.js): RG — нормаль, B — мокро/капля
uniform vec4 u_winRect, u_boatRect, u_fishRect;

const vec4 GLASS = vec4(18., 34., 206., 234.);
const float MUL_X = 112.;
const float SILL_BOT = 252.;
const float SEA_TOP = 406.;
const float SEA_BOT = 497.;
const vec2 PUD = vec2(112., 452.);          // центр лужи (под окном, на уровне будущего моря)
const float BS = 0.95;
const vec3 GOLD = vec3(.79, .64, .15);
float px;

vec3 hash3(vec2 p){ vec3 q = fract(vec3(p.xyx) * vec3(.1031, .1030, .0973)); q += dot(q, q.yxz + 33.33); return fract((q.xxy + q.yzz) * q.zyx); }
float noise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3. - 2. * f);
  float a = hash3(i).x, b = hash3(i + vec2(1, 0)).x, c = hash3(i + vec2(0, 1)).x, d = hash3(i + vec2(1, 1)).x;
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y); }
float cover(float sd){ return clamp(.5 - sd / px, 0., 1.); }
vec4 over(vec4 acc, vec3 c, float a){ return vec4(c * a + acc.rgb * (1. - a), a + acc.a * (1. - a)); }
vec4 overP(vec4 acc, vec4 s){ return s + acc * (1. - s.a); }
vec2 rot(vec2 q, float a){ float c = cos(a), s = sin(a); return vec2(c * q.x + s * q.y, -s * q.x + c * q.y); }
vec4 tex(sampler2D t, vec4 r, vec2 q){ vec2 uv = (q - r.xy) / r.zw; if (uv.x < 0. || uv.y < 0. || uv.x > 1. || uv.y > 1.) return vec4(0.); return texture2D(t, uv); }
// «живая акварель»: рисунок медленно течёт (смещение по шуму), пигмент перетекает пятнами, зерно бумаги
vec4 wc(sampler2D t, vec4 r, vec2 q){
  vec2 w = 2.2 * vec2(noise(q * .045 + vec2(u_time * .22, 0.)) - .5, noise(q * .045 + vec2(3.7, u_time * .19)) - .5);
  vec4 c = tex(t, r, q + w);
  float blot = noise(q * .03 + vec2(u_time * .10, -u_time * .07));
  c.rgb *= 1. - .30 * (blot - .5);
  c.rgb *= 1. - .07 * noise(q * 2.3);
  return c;
}
float glow(sampler2D t, vec4 r, vec2 q, float rad){
  float m = 0.;
  for (int i = 0; i < 12; i++){ float a = float(i) * .5236; m = max(m, tex(t, r, q + vec2(cos(a), sin(a)) * rad).a); }
  return clamp(m - tex(t, r, q).a, 0., 1.);
}

// ---------- капли на стекле: берутся из симуляции (glass-sim.js) ----------
// x — внутри капли, y — мокро (капля или свежая дорожка: запотевание стёрто), zw — смещение-линза
vec4 drops(vec2 g, vec2 gs){
  vec2 uv = clamp(g / gs, 0., 1.);
  vec4 d = texture2D(u_tDrops, vec2(uv.x, min(uv.y * .5, .498)));      // верхняя полоса карты
  float isDrop = smoothstep(.86, .97, d.b);
  float wet = clamp(d.b / .67, 0., 1.);
  vec2 n = (d.rg - .5) * 2.;
  return vec4(isDrop, max(wet, isDrop), -n * 4. * isDrop);
}
// краска, которую капли смыли с детей (нижняя полоса карты): rgb — цвет, a — сколько
vec4 paintOnGlass(vec2 g, vec2 gs){
  vec2 uv = clamp(g / gs, 0., 1.);
  return texture2D(u_tDrops, vec2(uv.x, max(.5 + uv.y * .5, .502)));
}

// ---------- комната за стеклом: стена, лампа, дети (растворяются), вода в комнате ----------
// дети + две подвижные ладошки (лежат в углу текстуры, см. drawKidsFront(split))
vec4 kidsAt(vec2 kq){
  vec4 k = wc(u_tKids, u_winRect, kq);
  vec2 hq = kq - vec2(96., 140. + u_hand);                    // Тамар ведёт пальцем вниз за каплей
  if (dot(hq, hq) < 420.){ vec4 pm = wc(u_tKids, u_winRect, hq + vec2(232., 282.)); k = pm + k * (1. - pm.a); }
  vec2 uq = kq - vec2(190., 150.) - u_uri.xy;                  // Ури прижимает ладошку к пятну
  if (dot(uq, uq) < 480.){ vec4 pm = wc(u_tKids, u_winRect, uq + vec2(40., 282.)); k = pm + k * (1. - pm.a); }
  return k;
}
vec3 interior(vec2 q){
  float lvl = GLASS.w - u_roomWater * 230.;
  bool under = u_variant > .5 && q.y > lvl;
  if (under) q += vec2(1.6 * sin(q.y * .2 + u_time * 3.), 1.2 * cos(q.x * .17 + u_time * 2.4));
  vec4 room = wc(u_tRoom, u_winRect, q);
  vec3 c = room.rgb + vec3(.92, .84, .70) * (1. - room.a);
  float d = u_dissolve;                                      // краску смывает: тянется вниз и тает
  vec2 kq = q;
  vec4 kid;
  if (u_flight > 1.5){                                       // смыв дождём: краску уносит струйками сверху вниз
    float col = noise(vec2(q.x * .32, 1.7)), col2 = noise(vec2(q.x * .9, 4.1));
    kq.y -= d * 5. * col;
    kid = kidsAt(kq);
    float front = mix(80., 270., d) + 30. * (col - .5) + 12. * (col2 - .5);
    kid *= smoothstep(front - 22., front + 6., q.y + 8. * noise(q * .15));   // выше «фронта» краски уже нет
    kid *= 1. - .3 * d;                                                     // и вся бледнеет, разбавляясь
  } else {
    if (d > 0.) kq.y -= d * 26. * noise(q * vec2(.12, .05));
    kid = kidsAt(kq);
    kid *= 1. - smoothstep(-.15, .15, d * 1.35 - noise(q * .07) - (q.y - 120.) / 400.);
  }
  c = kid.rgb + c * (1. - kid.a);
  if (u_variant > .5 && u_roomWater > 0.){
    float s = lvl + 2. * sin(q.x * .12 + u_time * 2.5);
    if (q.y > s){
      c = mix(c, vec3(.36, .55, .66), .55);
      c += .3 * smoothstep(2., 0., abs(q.y - s - 1.));
      vec2 b = q * vec2(.25, .12) + vec2(0., u_time * 1.6);
      vec3 h = hash3(floor(b));
      c += .4 * smoothstep(.12, 0., length(fract(b) - .5 - (h.xy - .5) * .5) - .04) * step(.75, h.z);
    }
  }
  return c;
}

vec4 windowLayer(vec2 p, vec4 acc){
  vec2 gs = GLASS.zw - GLASS.xy;
  if (p.x >= GLASS.x && p.x <= GLASS.z && p.y >= GLASS.y && p.y <= GLASS.w){
    float th = u_open * 1.25, ct = cos(th), st = sin(th);
    vec2 local = vec2(-1.); float sash = 0.;
    float wl = MUL_X - GLASS.x, wr = GLASS.z - MUL_X, cy = (GLASS.y + GLASS.w) * .5;
    float ul = (p.x - GLASS.x) / max(wl * ct, .001);
    if (ul >= 0. && ul <= 1.){ float sy = 1. + .16 * st * ul; vec2 l = vec2(GLASS.x + ul * wl, cy + (p.y - cy) / sy); if (l.y >= GLASS.y && l.y <= GLASS.w){ local = l; sash = 1.; } }
    float ur = (GLASS.z - p.x) / max(wr * ct, .001);
    if (sash < .5 && ur >= 0. && ur <= 1.){ float sy = 1. + .16 * st * ur; vec2 l = vec2(GLASS.z - ur * wr, cy + (p.y - cy) / sy); if (l.y >= GLASS.y && l.y <= GLASS.w){ local = l; sash = 1.; } }
    vec3 c;
    if (sash > .5){
      vec4 dr = drops(local - GLASS.xy, gs);
      c = interior(p + dr.zw * 1.5);          // капля — линза: комната в ней перевёрнута и увеличена
      float fog = .32 * (1. - dr.y);
      float breath = (1. - u_dissolve) * ((.5 + .5 * sin(u_time * 2.1)) * smoothstep(13., 3., length(local - vec2(66., 166.)))
                   + (.5 + .5 * sin(u_time * 2.1 + 2.)) * smoothstep(13., 3., length(local - vec2(160., 175.))));
      float fogA = clamp(fog + breath * .45, 0., .8);
      if (u_uri.z > 0.){                                         // Ури надышал пятно; ладошка оставляет в нём отпечаток
        float bp = u_uri.z * smoothstep(29., 15., length((local - vec2(174., 178.)) * vec2(1., 1.15)) + 4. * (noise(local * .18) - .5));
        fogA = max(fogA, .93 * bp * (1. - dr.y * .5));
        if (u_uri.w > 0.){
          vec2 pq = local - vec2(176., 176.) + vec2(40., 282.);
          float pm = tex(u_tKids, u_winRect, pq).a;
          float rim = clamp(glow(u_tKids, u_winRect, pq, 1.6), 0., 1.);       // по краю отпечатка конденсат гуще
          float k = u_uri.w * min(1., u_uri.z * 1.5);
          fogA = mix(fogA, min(.97, fogA + .4 * step(.05, bp)), rim * k);
          fogA *= 1. - k * smoothstep(.2, .7, pm);
        }
        c = mix(c, vec3(.80, .86, .92), fogA * bp * .9);     // надышанное пятно — холоднее и плотнее, тёплая ладонь в нём видна
        fogA *= 1. - bp * .9;
      }
      c = mix(c, vec3(.86, .88, .89), fogA);
      if (dr.x > .02){
        vec2 q = -dr.zw;                                        // q — нормаль купола ×6
        vec3 cd = c * mix(1., .8, smoothstep(2.6, 3.9, length(q)) * (.5 + .5 * smoothstep(1., -2., q.y)));   // тень по верхнему краю
        cd += smoothstep(1.1, 0., length(q - vec2(-1.3, -1.9))) * .45;         // блик сверху-слева
        cd += smoothstep(1.8, 0., length(q - vec2(.4, 2.4))) * .12;            // светлый «отсвет» внизу
        c = mix(c, cd, dr.x);
      }
      if (u_flight > 1.5){ vec4 pc = paintOnGlass(local - GLASS.xy, gs); c = mix(c, pc.rgb, pc.a * .62); }
      c = mix(c, vec3(.74, .80, .86), .10 + .08 * smoothstep(.3, 1., fract((local.x + local.y) * .006)));
      acc = over(acc, c, 1.);
      acc = overP(acc, wc(u_tSash, u_winRect, local));
      if (st > .02) acc = over(acc, vec3(.2, .22, .25), .25 * st * (1. - smoothstep(0., 6., abs(p.x - (GLASS.x + wl * ct)))));
    } else acc = over(acc, interior(p), 1.);
  }
  vec2 pe = p;                                                    // герань кивает под каплями и ветром
  if (p.y > 178. && p.y < 214. && p.x > 8. && p.x < 216.) pe.x += (214. - p.y) / 34. * 1.8 * sin(u_time * 2.4 + p.x * .08) * (.45 + .55 * u_wind);
  acc = overP(acc, wc(u_tExt, u_winRect, pe));
  if (u_hl == 1.) acc = over(acc, GOLD, glow(u_tExt, u_winRect, p, 5.) * .7);
  return acc;
}

// ---------- вариант 0: тяжёлые капли с подоконника и лужа ----------
float dripX(float fi){ return 58. + fi * 36. + 6. * sin(fi * 3.1); }
float dripPer(float fi){ return .95 + .25 * fract(fi * .618); }
// капли с козырька: бусинка бежит по кромке к углу, там набухает капля и срывается на подоконник
vec4 waterDrop(vec4 acc, vec2 p, vec2 c, float r, float st){
  vec2 q = (p - c) / vec2(r, r * st);
  float d = length(q) - 1.;
  if (d < .6) acc = over(acc, vec3(.55, .68, .77) + .45 * smoothstep(.5, 0., length(q + vec2(.35, .45))), cover(d * r) * .85);
  return acc;
}
vec4 canopyLayer(vec2 p, vec4 acc){
  if (p.y < 14. || p.y > 238. || (p.y > 30. && p.x > 8. && p.x < 216.)) return acc;
  for (int i = 0; i < 2; i++){
    float fi = float(i), side = i == 0 ? -1. : 1., x = i == 0 ? 1.5 : 222.5, y0 = 22.5;
    float per = i == 0 ? 1.7 : 2.3, ph = mod(u_time + fi * .9, per), grow = per - .7;
    if (ph < grow){
      float g = ph / grow, r = .6 + 1.7 * g;
      if (g < .8) acc = waterDrop(acc, p, vec2(mix(x - side * 70., x, smoothstep(0., .8, g)), y0 + .9), .9, 1.);
      acc = waterDrop(acc, p, vec2(x, y0 + r * (.5 + .6 * g)), r, 1. + .5 * g);
    } else {
      float tf = ph - grow, y = y0 + 3. + 450. * tf * tf;
      if (y < 236.) acc = waterDrop(acc, p, vec2(x, y), 2.1, 1. + min(1.2, tf * 3.));
    }
  }
  return acc;
}
vec4 dripLayer(vec2 p, vec4 acc, float surf){
  if (u_drip <= 0.) return acc;
  for (int i = 0; i < 4; i++){
    float fi = float(i);
    float t = mod(u_time + fi * .37, dripPer(fi));
    float y = SILL_BOT + 2. + 450. * t * t;
    if (y > surf) continue;
    float stretch = 1. + clamp((y - SILL_BOT) / 80., 0., 1.6);
    vec2 q = (p - vec2(dripX(fi), y)) / vec2(2.4, 2.4 * stretch);
    float d = length(q) - 1.;
    if (d < .5) acc = over(acc, vec3(.48, .64, .74) + .45 * smoothstep(.5, 0., length(q + vec2(.35, .45))), cover(d * 2.4) * .85 * u_drip);
  }
  return acc;
}
// Вариант 0: одно тело воды. Пока u_morph = 0 — лужа (вид чуть сверху: эллипс с неровным краем, круги
// от капель, отражение неба); с ростом u_morph её верхний край непрерывно поднимается в волнистую
// поверхность моря, нижний — опускается ко дну, цвет перетекает в морской. Склейки двух слоёв нет.
vec4 waterLayer0(vec2 p, vec4 acc, float front){
  float R = u_puddleR * 520.;
  if (R < .5) return acc;
  float m = u_morph;
  vec2 d = p - PUD;
  float ry = min(R * .42, 45.);
  float ex = d.x / R;
  float inside = mix(1. - ex * ex, 1., m);
  if (inside <= 0.) return acc;
  float edgeN = 1. + .14 * (noise(vec2(p.x * .045 + 3., u_time * .35)) - .5) * 2. * (1. - m) * smoothstep(420., 80., R);
  float hh = ry * sqrt(inside) * edgeN;
  float wave = m * (3. * sin(p.x * .045 - u_time * 1.8) + 1.6 * sin(p.x * .11 + u_time * 1.3) + .7 * sin(p.x * .27 - u_time * 2.7));
  float top = mix(PUD.y - hh, SEA_TOP + wave, m);
  float bot = mix(PUD.y + hh, SEA_BOT, m);
  // бегущие валики от капель на поверхности (когда это уже море)
  for (int i = 0; i < 4; i++){
    float fi = float(i), per = dripPer(fi);
    float tf = sqrt(max(0., (top - SILL_BOT - 2.) / 450.));
    float tt = mod(u_time + fi * .37 - tf, per);
    float dx = abs(p.x - dripX(fi)) - tt * 60.;
    top -= m * u_drip * 2.2 * exp(-dx * dx * .02) * exp(-tt * 2.5);
  }
  if (u_splashT >= 0. && u_splashT < 1.4){                     // круг от прыжка рыбки
    float dxs = abs(p.x - u_splashX) - u_splashT * 42.;
    top -= 2.6 * exp(-dxs * dxs * .03) * exp(-u_splashT * 2.6);
  }
  if (front > .5){ if (m < .999) return acc; top = max(top, u_boat.y + 6.); }
  // низ моря — акварельный край: неровный, с тёмной каймой собравшегося пигмента
  float yb = SEA_BOT - 3. + 6. * (noise(vec2(p.x * .045, 2.3)) - .5) * 2. + 2. * (noise(vec2(p.x * .22, 5.)) - .5);
  float bottomA = mix(cover(p.y - bot), cover(p.y - yb), m);
  float a = cover(top - p.y) * bottomA;
  if (a <= 0.) return acc;
  // цвет лужи: отражение неба, светлее у дальнего края, мениск-блик по кромке, круги от капель
  vec3 cp = mix(vec3(.74, .81, .86), vec3(.52, .65, .73), clamp((p.y - top) / max(bot - top, 1.), 0., 1.));
  cp += .32 * smoothstep(2.2, 0., min(abs(p.y - top), abs(p.y - bot)));
  for (int i = 0; i < 4; i++){
    float fi = float(i), per = dripPer(fi);
    float tf = sqrt(max(0., (PUD.y - 10. - SILL_BOT - 2.) / 450.));
    float tt = mod(u_time + fi * .37 - tf, per);
    vec2 rq = vec2(p.x - dripX(fi), (p.y - PUD.y + 10.) / .42);
    cp += .3 * u_drip * smoothstep(1.6, 0., abs(length(rq) - tt * 55.)) * exp(-tt * 2.2);
  }
  // в луже отражается окно (перевёрнутое, сжатое, дрожит от кругов) — пока это лужа
  if (m < .999){
    vec2 src = vec2(p.x + 1.4 * sin(p.y * .5 + u_time * 4.) * u_drip, SILL_BOT + 4. - (p.y - top) * 2.3);
    vec3 wcol = vec3(0.); float wa = 0.;
    if (src.x > GLASS.x && src.x < GLASS.z && src.y > GLASS.y && src.y < GLASS.w){ wcol = interior(src); wa = 1.; }
    vec4 ex = tex(u_tExt, u_winRect, src);
    wcol = ex.rgb + wcol * (1. - ex.a); wa = max(wa, ex.a);
    cp = mix(cp, mix(cp, wcol, .6) * .94, wa * .5 * (1. - m));
  }
  // цвет моря: глубина, пена по гребню, отблески; акварель — кайма у дна, зерно, светлые затёки
  float depth = clamp((p.y - top) / 80., 0., 1.);
  vec3 cs = mix(vec3(.52, .67, .75), vec3(.33, .48, .58), depth);
  cs *= 1. - .26 * smoothstep(5., 0., yb - p.y);
  cs *= 1. - .07 * noise(p * 1.8);
  cs += .07 * (noise(p * .03 + vec2(9., u_time * .05)) - .5);
  if (front < .5){
    cs += .45 * smoothstep(2.4, 0., abs(p.y - top - 1.2));
    cs += .12 * smoothstep(.96, 1., fract(p.x * .031 + p.y * .07 - u_time * .5)) * (1. - depth);
    if (u_boat.w > 0.){
      vec2 rp = vec2(p.x + 2.2 * sin(p.y * .35 + u_time * 3.), 2. * u_boat.y - p.y + 4.);
      vec4 r = tex(u_tBoat, u_boatRect, rot(rp - u_boat.xy, -u_boat.z) / BS);
      cs = mix(cs, mix(cs, r.rgb / max(r.a, .001), .55) * .85, r.a * .45 * (1. - smoothstep(0., 60., p.y - top)));
    }
  }
  vec3 c = mix(cp, cs, smoothstep(0., 1., m));
  acc = over(acc, c, a * (front > .5 ? .72 : mix(.92, 1., m)));
  if (u_hl == 2. && front < .5) acc = over(acc, GOLD, smoothstep(7., 0., abs(p.y - top)) * .6 * a);
  return acc;
}

// ---------- вариант 1: водопад из распахнутого окна ----------
vec4 fallLayer(vec2 p, vec4 acc){
  if (u_fall <= 0.) return acc;
  float yEnd = mix(SILL_BOT, u_seaTop, min(1., u_fall * 1.4));
  float shrink = smoothstep(.6, 1., u_fall);
  float x0 = mix(34., 96., shrink), x1 = mix(190., 128., shrink);
  float flare = (p.y - SILL_BOT) * .08;
  float sd = max(max(x0 - flare - p.x, p.x - x1 - flare), max(SILL_BOT - 2. - p.y, p.y - yEnd));
  if (sd > 1.) return acc;
  float thin = (1. - shrink * .45) * (1. - smoothstep(1., 1.35, u_fall));
  float streak = noise(vec2(p.x * .35, p.y * .03 - u_time * 4.5));
  vec3 c = mix(vec3(.36, .53, .64), vec3(.80, .89, .93), smoothstep(.55, .9, streak));
  acc = over(acc, c, cover(sd) * (.55 + .35 * streak) * thin);
  float foam = smoothstep(14., 0., abs(p.y - yEnd + 2.)) * smoothstep(x0 - 20., x0, p.x) * smoothstep(x1 + 20., x1, p.x);
  foam *= .6 + .4 * noise(vec2(p.x * .3 - u_time * 2., p.y * .4 + u_time * 3.));
  return over(acc, vec3(.93, .96, .97), foam * .8 * thin);
}

vec4 seaLayer(vec2 p, vec4 acc, float front){
  float top = u_seaTop;
  float k = u_variant < .5 ? u_morph : 1.;                 // вариант 0: море «поднимается» из лужи
  if (top >= SEA_BOT - .5 || k <= 0.) return acc;
  float wave = (3. * sin(p.x * .045 - u_time * 1.8) + 1.6 * sin(p.x * .11 + u_time * 1.3) + .7 * sin(p.x * .27 - u_time * 2.7)) * k;
  float s = top + wave * smoothstep(SEA_BOT, SEA_BOT - 30., top);
  if (front > .5) s = max(s, u_boat.y + 6.);
  float a = cover(s - p.y) * smoothstep(SEA_BOT + 6., SEA_BOT - min(34., (SEA_BOT - top) * .45), p.y) * k;
  if (a <= 0.) return acc;
  float depth = clamp((p.y - s) / 80., 0., 1.);
  vec3 c = mix(vec3(.52, .67, .75), vec3(.33, .48, .58), depth);
  if (front < .5){
    c += .45 * smoothstep(2.4, 0., abs(p.y - s - 1.2));
    c += .12 * smoothstep(.96, 1., fract(p.x * .031 + p.y * .07 - u_time * .5)) * (1. - depth);
    if (u_boat.w > 0.){
      vec2 rp = vec2(p.x + 2.2 * sin(p.y * .35 + u_time * 3.), 2. * u_boat.y - p.y + 4.);
      vec4 r = tex(u_tBoat, u_boatRect, rot(rp - u_boat.xy, -u_boat.z) / BS);
      c = mix(c, mix(c, r.rgb / max(r.a, .001), .55) * .85, r.a * .45 * (1. - smoothstep(0., 60., p.y - s)));
    }
  }
  acc = over(acc, c, a * (front > .5 ? .72 : 1.));
  if (u_hl == 2. && front < .5) acc = over(acc, GOLD, smoothstep(7., 0., abs(p.y - s)) * .6 * a);
  return acc;
}

// ---------- дождь по всей странице: косые нити + акварельные пятнышки на бумаге, которые сохнут ----------
vec4 pageRainLayer(vec2 p, vec4 acc){
  if (u_pageRain <= 0.) return acc;
  // пятнышко от капли: рваный край, вытянуто по удару (дождь косой), брызги-спутники, неровная кайма
  vec2 cell = floor(p / 46.);
  vec3 n = hash3(cell + 41.);
  float per = 2.6 + 2.4 * n.x, tt = mod(u_time + n.y * per, per);
  vec3 h = hash3(cell + floor((u_time + n.y * per) / per) * 3.1);
  vec2 ctr = (cell + .2 + .6 * h.xy) * 46.;
  vec2 dir = normalize(vec2(.16, 1.)), side = vec2(dir.y, -dir.x);
  vec2 q = p - ctr; q = vec2(dot(q, side), dot(q, dir) / 1.3);
  float ang = atan(q.y, q.x);
  float R0 = (2. + 3.2 * n.z) * smoothstep(0., .22, tt);
  float rr = R0 * (1. + .38 * (noise(vec2(ang * 1.7 + h.z * 10., h.x * 5.)) - .5) * 2.);
  float d = length(q) - rr;
  float dry = 1. - smoothstep(.4, per * .9, tt);
  float rim = .6 + .9 * noise(vec2(ang * 2.2, h.y * 7.));
  float spot = cover(d), ring = smoothstep(1.1, 0., abs(d + .5)) * rim;
  for (int j = 0; j < 3; j++){
    vec3 s3 = hash3(cell + vec2(float(j) * 1.7, h.z * 9.));
    float sa = s3.x * 6.283, sd = R0 * (1.5 + 1.2 * s3.y);
    float sr = R0 * (.12 + .18 * s3.z);
    spot = max(spot, cover(length(q - vec2(cos(sa), sin(sa)) * sd) - sr));
  }
  acc = over(acc, vec3(.55, .66, .74), spot * .22 * dry * u_pageRain);
  acc = over(acc, vec3(.40, .52, .62), ring * .2 * dry * u_pageRain);
  return acc;
}

float streaks(vec2 p){
  float a = 0.;
  for (int L = 0; L < 2; L++){
    float cs = L == 0 ? 14. : 23.;
    vec2 q = vec2(p.x + p.y * (.16 + .3 * u_gust), p.y);
    float col = floor(q.x / cs);
    vec3 m = hash3(vec2(col, float(L) * 9.));
    float speed = 420. + 180. * m.x, period = 110. + 90. * m.z;
    float y = mod(p.y - u_time * speed - m.y * 900., period);
    float len = 11. + 8. * m.z;
    float x = mod(q.x, cs) - cs * (.3 + .4 * m.y);
    a += smoothstep(.55, 0., abs(x)) * smoothstep(0., 3., y) * smoothstep(len, len - 5., y) * (L == 0 ? .30 : .20) * (1. + .7 * u_gust);
  }
  return clamp(a, 0., 1.);
}

// брызги от прыжка рыбки: капли разлетаются веером и падают обратно
vec4 splashLayer(vec2 p, vec4 acc){
  if (u_splashT < 0. || u_splashT > 1.) return acc;
  for (int i = 0; i < 8; i++){
    vec3 h = hash3(vec2(float(i), 5.5));
    float ang = -1.5708 + (h.x - .5) * 1.9, v = 70. + 60. * h.y, t = u_splashT;
    vec2 pos = vec2(u_splashX, SEA_TOP + 2.) + vec2(cos(ang), sin(ang)) * v * t + vec2(0., 260. * t * t);
    if (pos.y > SEA_TOP + 6.) continue;
    float r = mix(2.2, 1., t) * (.7 + .5 * h.z);
    acc = over(acc, vec3(.58, .72, .80) + .3 * smoothstep(r, 0., length(p - pos + .5)), cover(length(p - pos) - r) * .85 * (1. - smoothstep(.7, 1., t)));
  }
  return acc;
}

// вариант-переключатель: капли краски с тающих у окна детей летят с дождём к лодке и собираются в них
vec4 flightLayer(vec2 p, vec4 acc){
  if (u_flight < .5 || u_flight > 1.5 || u_boat.w <= 0.) return acc;
  for (int i = 0; i < 36; i++){
    float fi = float(i);
    vec3 h = hash3(vec2(fi, 3.3)), h2 = hash3(vec2(fi, 8.1));
    float t0 = 6.25 + h.z * .6, dur = .85 + .35 * h.x;
    float u = (u_time - t0) / dur;
    if (u < 0. || u > 1.) continue;
    vec2 a = vec2(44. + h.x * 150., 132. + h.y * 92.);
    vec2 b = u_boat.xy + vec2(-36. + h2.x * 84., -74. + h2.y * 42.) * BS;
    vec2 c = (a + b) * .5 + vec2(0., -80. - 50. * h2.z);
    float e = u * u * (3. - 2. * u);
    vec2 pos = mix(mix(a, c, e), mix(c, b, e), e);
    vec4 col = tex(u_tKids, u_winRect, a);
    vec3 cc = col.a > .15 ? col.rgb / col.a : vec3(.85, .62, .52);
    float r = mix(2.8, 1.5, e) * (1. - smoothstep(.85, 1., u));
    float d = length((p - pos) * vec2(1., .8)) - r * (1. + .25 * (noise(p * .6 + fi) - .5));
    acc = over(acc, cc, cover(d) * .85);
  }
  return acc;
}

// порывы ветра: завиток тушью-акварелью с волнистым хвостом летит по странице справа налево
vec4 gustLayer(vec2 p, vec4 acc){
  if (u_wind <= 0.) return acc;
  float a = 0.;
  for (int i = 0; i < 3; i++){
    float fi = float(i), per = 3.4 + fi * .7;
    float tt = mod(u_time + fi * 1.3, per), life = tt / 2.6;
    if (life > 1.) continue;
    vec3 h = hash3(vec2(floor((u_time + fi * 1.3) / per), fi * 7.));
    float y0 = 110. + h.x * 440., xh = 650. - life * 780.;
    float L = 140. + 60. * h.y, A = 6. + 6. * h.z, fade = smoothstep(0., .15, life) * smoothstep(1., .75, life);
    float g = 0.;
    float dx = p.x - xh;
    if (dx > 0. && dx < L){
      float ph = dx * .035 + u_time * 2. + h.z * 6.;
      float dist = abs(p.y - (y0 + A * sin(ph))) / sqrt(1. + pow(A * .035 * cos(ph), 2.));
      g = max(g, smoothstep(1.4, .2, dist) * smoothstep(L, L * .3, dx));
    }
    vec2 d = p - vec2(xh - 3., y0 - 9.);
    float r = length(d), th = atan(d.y, d.x) + 3.14159;
    for (int k = 0; k < 2; k++){
      float tk = th + 6.283 * float(k);
      if (tk < 8.) g = max(g, smoothstep(1.3, .2, abs(r - (2. + tk * 1.25))) * smoothstep(8., 5., tk));
    }
    a = max(a, g * fade);
  }
  return over(acc, vec3(.40, .48, .57), min(1., a * .45 * u_wind * (1. + .8 * u_gust)));
}

// ---------- смыв краски (вариант А переноса): цветные капли с подоконника → облака краски в море → к лодке ----------
vec3 paintCol(float fi){
  float k = mod(fi, 6.);
  return k < .5 ? vec3(.36, .47, .72) : k < 1.5 ? vec3(.47, .70, .55) : k < 2.5 ? vec3(.90, .62, .55)
       : k < 3.5 ? vec3(.86, .56, .30) : k < 4.5 ? vec3(.50, .36, .26) : vec3(.84, .30, .30);
}
float paintX(float fi){ return mod(fi, 2.) < .5 ? 52. + 30. * fract(fi * .618) : 138. + 34. * fract(fi * .382); }
const float PT0 = .12, PFALL = .585, PSWIM = 1.05;              // шаг между каплями, падение, путь к лодке
// краска стекает с подоконника струйками: голова бежит вниз к морю, потом струйка истончается и «досыхает» сверху
vec4 paintDripLayer(vec2 p, vec4 acc){
  if (u_paintT < 0. || p.y < SILL_BOT - 2. || p.y > u_seaTop + 4.) return acc;
  for (int i = 0; i < 6; i++){
    float fi = float(i), t = u_paintT - fi * PT0;
    if (t < 0. || t > 1.5) continue;
    float yh = min(SILL_BOT + 2. + 450. * t * t, u_seaTop + 2.);              // голова струйки
    float yt = SILL_BOT - 4.;                                                   // струйка держится за подоконник
    if (p.y > yh + 4. || p.y < yt) continue;
    float x0 = paintX(fi) + 1.6 * sin(p.y * .035 + fi * 2.1) + .5 * sin(p.y * .13 + fi);    // течёт почти прямо, чуть виляя
    float thin = 1. - .55 * smoothstep(.45, 1.2, t);                            // к концу истончается
    float w = (2. + 1.4 * fract(fi * .73)) * thin * (.8 + .4 * noise(vec2(p.y * .08, fi * 5.)));
    w *= .75 + .25 * smoothstep(SILL_BOT, SILL_BOT + 30., p.y);                // у подоконника чуть тоньше
    float dx = abs(p.x - x0);
    float hb = length((p - vec2(x0, yh)) / vec2(1.25, 1.7)) - w;                // капля-голова
    float sd = min(max(dx - w, p.y - yh), hb);
    if (sd > 2.) continue;
    float body = cover(sd) * (.55 + .3 * smoothstep(-1.5, 0., sd));              // светлее в середине, кайма у краёв
    vec3 col = paintCol(fi) * (.93 + .12 * noise(p * .3));
    acc = over(acc, col, body * .8 * (1. - smoothstep(.85, 1.5, t)));          // потом бледнеет, как разбавленная краска
  }
  return acc;
}
vec4 paintCloudLayer(vec2 p, vec4 acc){
  if (u_paintT < 0.) return acc;
  float a = 0.; vec3 cc = vec3(0.);
  for (int i = 0; i < 6; i++){
    float fi = float(i), u = (u_paintT - fi * PT0 - PFALL) / PSWIM;
    if (u < 0. || u > 1.) continue;
    vec3 h = hash3(vec2(fi, 2.7));
    // капля краски падает в море, клубится вниз, течение несёт её к лодке, и под лодкой она поднимается в неё
    vec2 st = vec2(paintX(fi), SEA_TOP + 6.);
    vec2 en = vec2(u_boat.x - 34. + 56. * h.x, u_boat.y + 10.);
    vec2 mid = vec2(mix(st.x, en.x, .45), SEA_TOP + 34. + 18. * h.y);
    float e = u * u * (3. - 2. * u);
    vec2 pos = mix(mix(st, mid, e), mix(mid, en, e), e);
    vec2 vel = normalize(mix(mid - st, en - mid, e) + vec2(.001));
    float grow = smoothstep(0., .3, u), sink = smoothstep(.68, 1., u);      // распускается — и уходит в лодку
    float r = mix(2.5, 15. + 6. * h.z, grow) * (1. - .7 * sink);
    vec2 dq = p - pos; vec2 lq = vec2(dot(dq, vel), dot(dq, vec2(-vel.y, vel.x)));
    lq.x = lq.x > 0. ? lq.x * 1.3 : lq.x * .5;                             // хвост тянется назад по течению
    float nz = noise(p * .07 + fi * 7. + vec2(u_paintT * .5, 0.)) + .5 * noise(p * .16 - fi * 3. + u_paintT * .8);
    float dd = length(lq * vec2(1., 1.35)) - r * (.75 + .5 * nz);
    float body = smoothstep(1.5, -3., dd) * (.26 + .30 * smoothstep(-6., 0., dd));   // мягкий край, светлее внутри, кайма пигмента
    body = clamp(body, 0., 1.) * (1. - sink);
    cc = mix(cc, paintCol(fi), body / max(a + body, .001)); a = max(a, body);
  }
  a *= smoothstep(u_seaTop - 1., u_seaTop + 5., p.y) * smoothstep(SEA_BOT + 2., SEA_BOT - 8., p.y);
  if (u_boat.w > 0.){                                                       // корпус лодки краской не пачкаем
    vec4 hull = tex(u_tBoat, u_boatRect, rot(p - u_boat.xy, u_boat.z) / BS);
    a *= 1. - hull.a;
  }
  return over(acc, cc * (.94 + .12 * noise(p * .15)), a);
}

void main(){
  vec2 c = vec2(gl_FragCoord.x, u_res.y - gl_FragCoord.y) / u_dpr;
  vec2 p = (c - vec2(u_pad, 0.)) / u_k;
  px = 1. / u_k;
  vec4 acc = vec4(0.);
  acc = pageRainLayer(p, acc);
  if (u_variant < .5) acc = waterLayer0(p, acc, 0.); else acc = seaLayer(p, acc, 0.);
  if (u_variant > .5) acc = fallLayer(p, acc);
  else acc = dripLayer(p, acc, u_morph > .5 ? u_seaTop : PUD.y - 10.);
  acc = paintDripLayer(p, acc);
  acc = windowLayer(p, acc);
  acc = canopyLayer(p, acc);
  if (u_boat.w > 0.){
    vec2 q = rot(p - u_boat.xy, u_boat.z) / BS;
    if (q.x < 4. && q.y < -55. && q.y > -172.){                   // одеяло-парус полощется на ветру
      float fr = clamp((4. - q.x) / 92., 0., 1.);
      q.x += fr * (1.9 * sin(q.y * .09 + u_time * 3.1) + 1.3 * sin(u_time * 1.7)) * (.35 + .65 * u_wind);
      q.y += fr * 1.3 * sin(q.x * .12 + u_time * 2.6) * (.35 + .65 * u_wind);
    }
    if (q.x < -48. && q.x > -78. && q.y < -12. && q.y > -56.)      // мишка покачивается
      q.x += clamp((-14. - q.y) / 40., 0., 1.) * 2.6 * sin(u_time * 2.3) * (.4 + .6 * u_wind);
    if (u_kidsBoat > 0.){                                   // дети проявляются, как краска на мокрой бумаге
      vec4 kd = wc(u_tBoatKids, u_boatRect, q);
      if (u_flight > 1.5){                                  // краска впитывается снизу вверх, как в мокрую бумагу
        float lv = mix(6., -100., u_kidsBoat) + 12. * (noise(q * vec2(.09, .05) + 3.) - .5);
        float on = smoothstep(lv - 3., lv + 3., q.y);
        kd.rgb *= 1. - .22 * smoothstep(6., 0., abs(q.y - lv - 2.)) * (1. - u_kidsBoat);   // кайма пигмента на фронте
        kd *= on;
      } else kd *= smoothstep(-.12, .12, u_kidsBoat * 1.4 - noise(q * .09) - .2);
      acc = overP(acc, kd * u_boat.w);
    }
    acc = overP(acc, wc(u_tBoat, u_boatRect, q) * u_boat.w);
    // подсветка: 3 — лодка (без паруса), 5 — только парус-одеяло
    float inSail = step(q.x, 9.) * step(-94., q.x) * step(q.y, -55.) * step(-166., q.y);
    if (u_hl == 3.) acc = over(acc, GOLD, glow(u_tBoat, u_boatRect, q, 4.) * .7 * (1. - inSail));
    if (u_hl == 5.) acc = over(acc, GOLD, glow(u_tBoat, u_boatRect, q, 4.) * .8 * inSail);
  }
  if (u_variant < .5) acc = waterLayer0(p, acc, 1.); else acc = seaLayer(p, acc, 1.);
  acc = paintCloudLayer(p, acc);
  if (u_fish.w > 0.){
    float peek = step(1.5, u_fish.w);
    vec2 fp = u_fish.xy + peek * vec2(0., 1.6 * sin(u_time * 2.6));
    vec2 q = rot(p - fp, u_fish.z + peek * .08 * sin(u_time * 1.9));
    float fa = peek > .5 ? cover(p.y - u_seaTop - 1. - 1.2 * sin(p.x * .045 - u_time * 1.8)) : u_fish.w;
    acc = overP(acc, wc(u_tFish, u_fishRect, q) * fa);
    if (u_hl == 4.) acc = over(acc, GOLD, glow(u_tFish, u_fishRect, q, 4.) * .7);
  }
  acc = splashLayer(p, acc);
  acc = flightLayer(p, acc);
  acc = gustLayer(p, acc);
  // нити дождя: по всей странице после того, как вода пролилась; вокруг окна — с самого начала
  float winZone = 1. - smoothstep(0., 46., max(max(-14. - p.x, p.x - 238.), max(-6. - p.y, p.y - 268.)));
  acc = over(acc, vec3(.62, .72, .80), streaks(p) * max(u_pageRain, u_rain * winZone * .85));
  gl_FragColor = acc;
}
