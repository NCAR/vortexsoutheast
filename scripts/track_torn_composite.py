import argparse
import atcf
import cartopy
from cartopy.mpl.gridliner import LONGITUDE_FORMATTER, LATITUDE_FORMATTER
import gridlines
import ibtracs
import itertools
import logging
import matplotlib.pyplot as plt
from metpy.units import units
import os
import pandas as pd 
import pdb
import spc

# =============Arguments===================
parser = argparse.ArgumentParser(description = "Plot NARR", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("-d", "--debug", action='store_true')
parser.add_argument("-e", "--extent", type=float, nargs=4, help="debug plot extent lonmin, lonmax, latmin, latmax", 
        default=[-105, -70, 20, 43])
parser.add_argument("--spctd", type=float, default=1.5, help="hours on both sides of valid time to show tornadoes")
parser.add_argument("--onetrackcolor", action="store_true", 
        help="instead of coloring track segments by intensity, color entire track (and tornadoes) the same color")
parser.add_argument("ifile", help="text file. each line starts with a storm name, followed by a yyyymmddhh time")

# Assign arguments to simple-named variables
args = parser.parse_args()
debug = args.debug
extent = args.extent
ifile = args.ifile
onetrackcolor = args.onetrackcolor
spc_td = pd.to_timedelta(args.spctd, unit='hours')

level = logging.DEBUG if debug else logging.INFO
logging.basicConfig(format='%(asctime)s %(message)s', level=level)

logging.debug(args)

def tc_coords(df, trackdf):
    """
    get distance from TC center for each storm report in df
    """
    i = trackdf.valid_time == df.name
    assert i.sum(), f"no track times match {df.name}"
    originlon = trackdf.loc[i,"lon"].values[0] * units.degrees_E
    originlat = trackdf.loc[i,"lat"].values[0] * units.degrees_N
    dist_from_origin, heading = spc.gdist_bearing(originlon, originlat, 
            df["slon"].values * units.degrees_E, df["slat"].values * units.degrees_N)
    df["dist_from_origin"] = dist_from_origin
    return df

logging.info(f"ifile {ifile}")
df = pd.read_csv(ifile, delim_whitespace=True, names=["stormname", "narrtime"])
df["narrtime"] = pd.to_datetime(df["narrtime"], format="%Y%m%d%H", utc=True)
df["year"] = df["narrtime"].dt.year

logging.info("load tornado reports (subsample later).")
all_storm_reports = spc.get_storm_reports(start=pd.to_datetime("19900101",utc=True), 
        end=pd.to_datetime("20220101",utc=True), event_types=["torn"])

# round storm report times to nearest narr
epoch = pd.to_datetime("19700101", utc=True)
f = ((all_storm_reports["time"] + spc_td - epoch) / (spc_td*2) ).astype(int)
all_storm_reports["time3h"] = epoch + f * (spc_td*2)

TCTOR = spc.getTCTOR()

logging.info("Load ibtracs (subsample later).")
all_tracks, best_track_file = ibtracs.get_df(basin="NA") # North Atlantic basin guaranteed for NARR

logging.info(f"got {len(all_tracks)} track data from {best_track_file}")
fig = plt.figure(figsize=(11,8.5))
axc = plt.axes(projection=cartopy.crs.LambertConformal())
base, ext = os.path.splitext(ifile)
axc.set_title(os.path.basename(base))
stormrpts = []

colors = [None]
if onetrackcolor:
    colors = plt.cm.tab20.colors
color = itertools.cycle(colors)

for (stormname, year), group in df.groupby(["stormname","year"]):
    onecolor = next(color)
    assert len(group) == 1, ("stormname/year combo should be unique in each input file."
                             "Do not use input file with all times of diurnal cycle. just 1st one")
    # first narrtime and 3-h intervals covering a diurnal cycle
    narrtime0 = pd.to_datetime(group.narrtime.values[0])
    narrtimes = pd.date_range(start=narrtime0, end=narrtime0 + pd.Timedelta(21, unit="hour"), freq="3H", tz='UTC')
    imatch = (all_tracks['stormname'] == stormname.upper()) & (all_tracks['valid_time'].dt.year == year) & (all_tracks["rad"] == 34)
    assert imatch.sum(), f"no matching IBTRACS for {stormname.upper()} {year}"
    trackdf = all_tracks[imatch]
    fmt='%Y%m%d %H%Mz'
    tracktimes = trackdf.valid_time
    logging.debug(f"{stormname} {year} {len(trackdf)} times "
                 f"[{tracktimes.min().strftime(fmt)},{tracktimes.max().strftime(fmt)}]")


    last_label = "" if onecolor else stormname

    # Plot dashed track from beginning to end of NARR dirurnal cycle
    first_label = "o"
    maxnarr = narrtimes.max()
    logging.debug(f"plot_track through {maxnarr}")
    trackdf = trackdf[tracktimes <= maxnarr]
    atcf.plot_track(axc, first_label, trackdf, last_label, label_interval_hours=0, scale=0.5, linestyle="dashed", onecolor=onecolor)

    # Plot solid track for NARR diurnal cycle
    number = group.index.values[0] + 1
    first_label = number if onecolor else "o"
    in_narr = trackdf.valid_time.isin(narrtimes)
    if in_narr.sum() < 8:
        logging.warning(f"only {in_narr.sum()} track times in {narrtime0.strftime(fmt)} diurnal cycle. checking extensions")
        trackdf = ibtracs.getExt(stormname,year,trackdf,narrtimes)
        in_narr = trackdf.valid_time.isin(narrtimes)
    trackdf = trackdf[in_narr] # drop tracktimes not in narrtimes array
    logging.info(f"plot_track #{number} {stormname} for {narrtime0.strftime(fmt)} diurnal cycle")
    atcf.plot_track(axc, first_label, trackdf, last_label, label_interval_hours=0, scale=1.6, onecolor=onecolor)

    start = narrtimes.min()-spc_td
    end   = narrtimes.max()+spc_td
    logging.debug(f"storm reports [{start.strftime(fmt)},{end.strftime(fmt)})")
    twin = (all_storm_reports.time >= start ) & (all_storm_reports.time < end)
    if not any(twin):
        logging.info(f"no storm reports")
        continue
    stormrpts_twin = all_storm_reports[twin]
    # restrict reports to within 800 km of TC
    s = stormrpts_twin.groupby("time3h", group_keys=False).apply(tc_coords,trackdf)
    s = s[s.dist_from_origin < 800]
    # restrict distance for some cases to match TCTOR
    if stormname == "Erin" and year == 2007:
        s = s[s.dist_from_origin < 700]
    if stormname == "Olga" and year == 2007:
        s = s[s.dist_from_origin < 688]
    if stormname == "Hermine" and year == 2004:
        s = s[s.dist_from_origin < 475]

    logging.info(f"{len(s)} storm reports near {stormname} {year}")

    TCTOR_twin = TCTOR[(TCTOR.time >= start) & (TCTOR.time < end) & (TCTOR["Tor-Center Dist km (from Worksheet)"] < 800) & (TCTOR["TC Name"] == f"{stormname}-{year%100:02d}")]
    if len(s) != len(TCTOR_twin):
        logging.warning(f"{len(s)} SPC, but {len(TCTOR_twin)} TCTOR reports")
        if abs(len(s) - len(TCTOR_twin)) > 1:
            print(s[["time","slat","slon","dist_from_origin"]])
            print(TCTOR_twin[["time","slat","slon","Tor-Center Dist km (from Worksheet)"]])
    stormrpts.append(s)
    legend_items = spc.plot(s, axc, scale=5, alpha=1, onecolor=onecolor)

if len(stormrpts):
    stormrpts = pd.concat(stormrpts)
else:
    logging.warning("no storm reports")
legend_items = spc.plot(stormrpts, axc, scale=0)
axc.legend(handles=legend_items.values())

axc.set_extent(extent, crs=cartopy.crs.PlateCarree()) 
# *must* call draw in order to get the axis boundary used to add ticks:
fig.canvas.draw()


# Define gridline locations and draw the lines using cartopy's built-in gridliner:
xticks = list(range(-160,-50,10))
yticks = list(range(0,65,5))
axc.gridlines(xlocs=xticks, ylocs=yticks, linewidth=0.2, linestyle='--')
axc.xaxis.set_major_formatter(LONGITUDE_FORMATTER) 
axc.yaxis.set_major_formatter(LATITUDE_FORMATTER)
gridlines.lambert_xticks(axc, xticks)
gridlines.lambert_yticks(axc, yticks)
axc.set_xlabel('')
axc.set_ylabel('')
axc.add_feature(cartopy.feature.STATES.with_scale('50m'), linewidth=0.18)
axc.add_feature(cartopy.feature.COASTLINE.with_scale('50m'), linewidth=0.22)
if not onetrackcolor:
    # Draw legend after set_extent, or it might be too high
    TCleg = atcf.TClegend(axc)

ofile = os.path.realpath(os.path.basename(base)+".ps")
if onetrackcolor:
    ofile = os.path.realpath(os.path.basename(base)+".colorbytrack.ps")
plt.savefig(ofile)
logging.info(f'created {ofile}')
