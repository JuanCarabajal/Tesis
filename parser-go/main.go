package main

import (
	"encoding/csv"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"log"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	dem "github.com/markus-wa/demoinfocs-golang/v5/pkg/demoinfocs"
	common "github.com/markus-wa/demoinfocs-golang/v5/pkg/demoinfocs/common"
	events "github.com/markus-wa/demoinfocs-golang/v5/pkg/demoinfocs/events"
	"gopkg.in/yaml.v3"
)

//
// ====== Mirage zones: tipos + loader + geom ======
//

type MapConfig struct {
	ZLayers []struct {
		Name string  `yaml:"name"`
		ZMin float64 `yaml:"z_min"`
		ZMax float64 `yaml:"z_max"`
	} `yaml:"z_layers"`
	Zones []Zone `yaml:"zones"`
}

type Zone struct {
	ID       string       `yaml:"id"`
	Layer    string       `yaml:"layer"`
	Polygon  [][2]float64 `yaml:"polygon"`
	Site     string       `yaml:"site"` // "A","B","MID" o vacío
	Key      string       `yaml:"key"`
	centroid [2]float64   // calculado
}

func loadMapConfig(path string) (*MapConfig, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		// permitir que falte el archivo
		if errors.Is(err, fs.ErrNotExist) {
			return nil, fs.ErrNotExist
		}
		return nil, err
	}
	var cfg MapConfig
	if err := yaml.Unmarshal(b, &cfg); err != nil {
		return nil, err
	}
	for i := range cfg.Zones {
		cfg.Zones[i].centroid = polygonCentroid(cfg.Zones[i].Polygon)
	}
	return &cfg, nil
}

// Ray casting: punto en polígono 2D
func pointInPolygon(x, y float64, poly [][2]float64) bool {
	n := len(poly)
	inside := false
	for i, j := 0, n-1; i < n; j, i = i, i+1 {
		xi, yi := poly[i][0], poly[i][1]
		xj, yj := poly[j][0], poly[j][1]
		intersect := ((yi > y) != (yj > y)) &&
			(x < (xj-xi)*(y-yi)/((yj-yi)+1e-9)+xi)
		if intersect {
			inside = !inside
		}
	}
	return inside
}

// Centroid de polígono (shoelace)
func polygonCentroid(poly [][2]float64) [2]float64 {
	n := len(poly)
	if n == 0 {
		return [2]float64{0, 0}
	}
	var area2, cx2, cy2 float64
	for i := 0; i < n; i++ {
		j := (i + 1) % n
		cross := poly[i][0]*poly[j][1] - poly[j][0]*poly[i][1]
		area2 += cross
		cx2 += (poly[i][0] + poly[j][0]) * cross
		cy2 += (poly[i][1] + poly[j][1]) * cross
	}
	if math.Abs(area2) < 1e-9 {
		// fallback: promedio simple
		var sx, sy float64
		for _, p := range poly {
			sx += p[0]
			sy += p[1]
		}
		return [2]float64{sx / float64(n), sy / float64(n)}
	}
	// A = area2/2; 1/(6A) = 1/(3*area2)
	return [2]float64{cx2 / (3 * area2), cy2 / (3 * area2)}
}

type zoneMatch struct {
	ID, Site string
	OK       bool
}

func classifyXY(cfg *MapConfig, x, y float64) zoneMatch {
	if cfg == nil {
		return zoneMatch{}
	}
	// Respeta el orden chico→grande definido en mirage.yml
	for _, z := range cfg.Zones {
		if pointInPolygon(x, y, z.Polygon) {
			site := z.Site
			if site == "" {
				site = "UNKNOWN"
			}
			return zoneMatch{ID: z.ID, Site: site, OK: true}
		}
	}
	return zoneMatch{}
}

func nearestZoneIfClose(cfg *MapConfig, x, y, threshold float64) zoneMatch {
	if cfg == nil {
		return zoneMatch{}
	}
	bestIdx := -1
	bestD2 := math.MaxFloat64
	for i, z := range cfg.Zones {
		dx, dy := x-z.centroid[0], y-z.centroid[1]
		d2 := dx*dx + dy*dy
		if d2 < bestD2 {
			bestD2 = d2
			bestIdx = i
		}
	}
	if bestIdx >= 0 && bestD2 <= threshold*threshold {
		z := cfg.Zones[bestIdx]
		site := z.Site
		if site == "" {
			site = "UNKNOWN"
		}
		return zoneMatch{ID: z.ID, Site: site, OK: true}
	}
	return zoneMatch{}
}

const nearestThreshold = 200.0

func zoneForXY(cfg *MapConfig, x, y float64) (zoneID, site string) {
	m := classifyXY(cfg, x, y)
	if !m.OK {
		m = nearestZoneIfClose(cfg, x, y, nearestThreshold)
	}
	if !m.OK {
		return "", "UNKNOWN"
	}
	return m.ID, m.Site
}

//
// ====== Parser ======
//

type roundInfo struct {
	startTick int
	endTick   int
	plantTick *int
	plantSite *string
	plantZone *string // NUEVO: sub-zona del plant
	written   bool    // evita duplicado RoundEnd / RoundEndOfficial
}

func sideOf(p *common.Player) string {
	if p == nil {
		return ""
	}
	switch p.Team {
	case common.TeamTerrorists:
		return "T"
	case common.TeamCounterTerrorists:
		return "CT"
	default:
		return ""
	}
}

func teamLabelOf(p *common.Player) string {
	// Para el contrato actual alcanza con T/CT
	return sideOf(p)
}

func must(err error) {
	if err != nil {
		log.Fatal(err)
	}
}

func main() {
	inPath := flag.String("in", "", "Ruta al .dem")
	outEvents := flag.String("out-events", "events.csv", "Ruta de salida events.csv")
	outRounds := flag.String("out-rounds", "rounds.csv", "Ruta de salida rounds.csv")
	mirageYAML := flag.String("map-yaml", "configs/maps/mirage.yml", "Ruta a mirage.yml")
	flag.Parse()

	if *inPath == "" {
		log.Fatal("Usar --in <demo.dem>")
	}

	// Abrir demo
	f, err := os.Open(*inPath)
	must(err)
	defer f.Close()

	p := dem.NewParser(f)
	defer p.Close()

	// Map name vía convars (si no aparece, queda vacío y no afecta)
	mapName := ""
	p.RegisterEventHandler(func(e events.ConVarsUpdated) {
		if v, ok := e.UpdatedConVars["map"]; ok {
			name := strings.ToLower(v)
			if strings.HasPrefix(name, "de_") {
				name = strings.TrimPrefix(name, "de_")
			}
			mapName = name
		}
		if v, ok := e.UpdatedConVars["host_map"]; ok && mapName == "" {
			name := strings.ToLower(v)
			if strings.HasPrefix(name, "de_") {
				name = strings.TrimPrefix(name, "de_")
			}
			mapName = name
		}
	})

	// Cargar mirage.yml (si no existe, seguimos sin zonas)
	var mirageCfg *MapConfig
	if cfg, err := loadMapConfig(*mirageYAML); err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			log.Printf("INFO: %s no encontrado; zonas deshabilitadas", *mirageYAML)
		} else {
			log.Printf("WARN: error cargando %s: %v (zonas deshabilitadas)", *mirageYAML, err)
		}
	} else {
		mirageCfg = cfg
	}

	// Writers
	evf, err := os.Create(*outEvents)
	must(err)
	defer evf.Close()
	evw := csv.NewWriter(evf)
	defer evw.Flush()

	rdf, err := os.Create(*outRounds)
	must(err)
	defer rdf.Close()
	rdw := csv.NewWriter(rdf)
	defer rdw.Flush()

	// Headers (agregamos columnas nuevas al final)
	evHeader := []string{
		"match_id", "round", "ts", "event",
		"team_killer", "team_victim", "side_killer", "side_victim",
		"killer", "victim", "assister", "is_flash_assist", "flashed_enemies", "nade_damage",
		"x", "y", "z", "map_name",
		// NUEVO:
		"attacker_zone_id", "attacker_site",
		"victim_zone_id", "victim_site",
		"event_zone_id", "event_site",
	}
	must(evw.Write(evHeader))

	rdHeader := []string{
		"match_id", "round", "start_ts", "end_ts", "plant_ts", "plant_site",
		// NUEVO:
		"plant_zone_id",
	}
	must(rdw.Write(rdHeader))

	// match_id = nombre del archivo sin extensión
	matchID := strings.TrimSuffix(filepath.Base(*inPath), filepath.Ext(*inPath))

	gs := p.GameState()
	currentRound := 0
	rounds := map[int]*roundInfo{}

	// ticks -> segundos desde inicio de ronda
	toSec := func(tick, start int) float64 {
		return float64(tick-start) * p.TickTime().Seconds()
	}

	writeRoundRow := func(r int) {
		ri, ok := rounds[r]
		if !ok || ri == nil || ri.written {
			return
		}
		// endTick puede no haberse seteado si la demo está truncada
		endTS := 0.0
		if ri.endTick != 0 {
			endTS = toSec(ri.endTick, ri.startTick)
		}

		var plantTS string
		if ri.plantTick != nil {
			plantTS = fmt.Sprintf("%.3f", toSec(*ri.plantTick, ri.startTick))
		} else {
			plantTS = ""
		}

		var plantSite string
		if ri.plantSite != nil {
			plantSite = *ri.plantSite
		} else {
			plantSite = ""
		}

		var plantZone string
		if ri.plantZone != nil {
			plantZone = *ri.plantZone
		} else {
			plantZone = ""
		}

		row := []string{
			matchID,
			strconv.Itoa(r),
			"0.000", // start_ts siempre 0 relativo al inicio de la ronda
			fmt.Sprintf("%.3f", endTS),
			plantTS,
			plantSite,
			plantZone, // NUEVO
		}
		must(rdw.Write(row))
		rdw.Flush()
		ri.written = true
	}

	// Handlers
	p.RegisterEventHandler(func(e events.RoundStart) {
		currentRound = gs.TotalRoundsPlayed() + 1
		rt := gs.IngameTick()
		rounds[currentRound] = &roundInfo{startTick: rt}
	})

	p.RegisterEventHandler(func(e events.RoundEnd) {
		if ri, ok := rounds[currentRound]; ok {
			if ri.endTick == 0 {
				ri.endTick = gs.IngameTick()
			}
			writeRoundRow(currentRound)
		}
	})

	// Algunas demos disparan RoundEndOfficial; prevenimos duplicado con 'written'
	p.RegisterEventHandler(func(e events.RoundEndOfficial) {
		if ri, ok := rounds[currentRound]; ok {
			if ri.endTick == 0 {
				ri.endTick = gs.IngameTick()
			}
			writeRoundRow(currentRound)
		}
	})

	p.RegisterEventHandler(func(e events.BombPlanted) {
		if ri, ok := rounds[currentRound]; ok {
			t := gs.IngameTick()
			ri.plantTick = &t
			site := "A"
			if e.Site == events.BombsiteB {
				site = "B"
			}
			ri.plantSite = &site

			// NUEVO: zona del plant (posición del planter)
			if planter := e.Player; planter != nil && mirageCfg != nil {
				pos := planter.Position()
				zid, _ := zoneForXY(mirageCfg, float64(pos.X), float64(pos.Y))
				if zid != "" {
					ri.plantZone = &zid
				}
			}
		}
	})

	p.RegisterEventHandler(func(e events.Kill) {
		ri, ok := rounds[currentRound]
		if !ok || ri == nil {
			return
		}
		nowTick := gs.IngameTick()
		ts := toSec(nowTick, ri.startTick)

		k := e.Killer
		v := e.Victim
		ass := e.Assister

		// coords ATTACKER y VICTIM (usamos victim para x,y,z de salida como tenías)
		ax, ay := 0.0, 0.0
		if k != nil {
			ap := k.Position()
			ax, ay = float64(ap.X), float64(ap.Y)
		}

		vx, vy, vz := 0.0, 0.0, 0.0
		if v != nil {
			vp := v.Position()
			vx, vy, vz = float64(vp.X), float64(vp.Y), float64(vp.Z)
		}

		// zonas
		attZone, attSite := "", "UNKNOWN"
		vicZone, vicSite := "", "UNKNOWN"
		if mirageCfg != nil {
			if k != nil {
				attZone, attSite = zoneForXY(mirageCfg, ax, ay)
			}
			if v != nil {
				vicZone, vicSite = zoneForXY(mirageCfg, vx, vy)
			}
		}

		row := []string{
			matchID,
			strconv.Itoa(currentRound),
			fmt.Sprintf("%.3f", ts),
			"kill",
			teamLabelOf(k),
			teamLabelOf(v),
			sideOf(k),
			sideOf(v),
			func() string { if k != nil { return k.Name } else { return "" } }(),
			func() string { if v != nil { return v.Name } else { return "" } }(),
			func() string { if ass != nil { return ass.Name } else { return "" } }(),
			"False", // is_flash_assist (se puede refinar luego)
			"0",     // flashed_enemies
			"0",     // nade_damage
			fmt.Sprintf("%.1f", vx),
			fmt.Sprintf("%.1f", vy),
			fmt.Sprintf("%.1f", vz),
			mapName,
			// NUEVO:
			attZone, attSite,
			vicZone, vicSite,
			"", "", // event_zone_id,event_site (vacío para kills)
		}
		must(evw.Write(row))
	})

	// Parsear todo
	err = p.ParseToEnd()
	if err != nil {
		// Ignorar demos truncadas y generar salida parcial
		if errors.Is(err, dem.ErrUnexpectedEndOfDemo) ||
			strings.Contains(err.Error(), "ErrUnexpectedEndOfDemo") ||
			strings.Contains(err.Error(), "unexpected EOF") {
			log.Printf("WARN: demo truncada: %v — genero salida parcial", err)
		} else {
			log.Fatal(err)
		}
	}

	// Si quedó una ronda sin cerrar (demo truncada), escribimos la fila igual
	if ri, ok := rounds[currentRound]; ok && !ri.written {
		writeRoundRow(currentRound)
	}

	// Flush final
	evw.Flush()
	rdw.Flush()
	fmt.Println("OK events ->", *outEvents)
	fmt.Println("OK rounds ->", *outRounds)
}
