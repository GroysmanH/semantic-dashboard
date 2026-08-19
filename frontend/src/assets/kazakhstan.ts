/** A simplified outline of Kazakhstan, bundled rather than fetched.
 *
 *  The app is self-hosted and has to work offline, so no CDN and no
 *  runtime download. It is deliberately coarse — around eighty vertices
 *  against the thousands a survey boundary carries — because its whole job
 *  is to tell the reader that the scatter of points in the west is the
 *  Caspian shore rather than an arbitrary cloud. The wells it sits under
 *  are synthetic too, so a precise border would be false precision under a
 *  false dataset. */
export const KAZAKHSTAN = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      properties: { name: "Kazakhstan" },
      geometry: {
        type: "Polygon",
        coordinates: [[
          [46.5, 48.5], [47.1, 50.3], [48.8, 50.6], [50.3, 51.3], [51.3, 51.5],
          [53.3, 51.5], [54.7, 51.0], [55.7, 50.6], [57.0, 51.0], [58.4, 51.1],
          [59.5, 50.5], [61.0, 50.8], [61.5, 51.3], [60.0, 51.9], [60.9, 52.5],
          [61.9, 52.9], [61.2, 53.6], [62.0, 54.0], [64.0, 54.4], [65.2, 54.4],
          [68.2, 54.9], [69.3, 55.4], [70.8, 55.3], [71.1, 54.2], [72.9, 54.1],
          [73.4, 53.5], [75.4, 54.1], [76.9, 54.5], [79.0, 54.9], [80.5, 51.3],
          [81.5, 50.7], [83.0, 50.9], [84.0, 50.1], [85.2, 49.6], [86.8, 49.1],
          [87.3, 49.1], [86.0, 48.5], [85.5, 47.2], [83.0, 47.2], [82.5, 45.2],
          [81.9, 45.2], [80.2, 45.0], [79.9, 44.9], [80.4, 44.0], [80.2, 42.9],
          [79.6, 42.5], [78.0, 42.8], [76.5, 43.0], [75.6, 42.8], [74.2, 43.2],
          [73.5, 42.5], [71.2, 42.8], [70.9, 42.2], [70.4, 41.5], [69.1, 41.4],
          [68.6, 40.7], [68.0, 41.0], [66.7, 41.2], [66.0, 41.9], [65.2, 43.0],
          [64.0, 43.7], [61.9, 43.5], [60.0, 44.8], [58.6, 45.6], [56.0, 45.0],
          [55.9, 44.9], [54.0, 42.2], [52.9, 41.9], [52.5, 42.7], [52.2, 41.8],
          [51.3, 41.6], [50.3, 44.2], [51.2, 44.5], [52.0, 45.5], [51.5, 46.0],
          [50.0, 46.5], [49.0, 46.3], [48.8, 46.6], [47.5, 46.6], [47.1, 47.8],
          [46.5, 48.5],
        ]],
      },
    },
  ],
} as const;
