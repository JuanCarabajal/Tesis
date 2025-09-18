package main

import (
	"encoding/csv"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	dem "github.com/markus-wa/demoinfocs-golang/v5/pkg/demoinfocs"
	events "github.com/markus-wa/demoinfocs-golang/v5/pkg/demoinfocs/events"
	common "github.com/markus-wa/demoinfocs-golang/v5/pkg/demoinfocs/common"
)

type roundInfo struct {
	startTick int
	endTick   int
	plantTick *int
	plantSite *string
	written   bool // para no escribir dos veces si llega RoundEnd y RoundEndOfficial
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
	flag.Parse()

	if *inPath == "" {
		log.Fatal("Usar --in <demo.dem>")
	}

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

	// Headers
	evHeader := []string{
		"match_id", "round", "ts", "event",
		"team_killer", "team_victim", "side_killer", "side_victim",
		"killer", "victim", "assister", "is_flash_assist", "flashed_enemies", "nade_damage",
		"x", "y", "z", "map_name",
	}
	must(evw.Write(evHeader))

	rdHeader := []string{"match_id", "round", "start_ts", "end_ts", "plant_ts", "plant_site"}
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

		row := []string{
			matchID,
			strconv.Itoa(r),
			"0.000", // start_ts siempre 0 relativo al inicio de la ronda
			fmt.Sprintf("%.3f", endTS),
			plantTS,
			plantSite,
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

		// coords del victim (estable para “zona de muerte”)
		x, y, z := 0.0, 0.0, 0.0
		if v != nil {
			pos := v.Position()
			x, y, z = float64(pos.X), float64(pos.Y), float64(pos.Z)
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
			fmt.Sprintf("%.1f", x),
			fmt.Sprintf("%.1f", y),
			fmt.Sprintf("%.1f", z),
			mapName,
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
