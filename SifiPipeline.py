#!/usr/bin/python

import subprocess
from Bio import SeqIO
import os
import json
from collections import Counter
import plotly.graph_objects as go

import sifi21INTA.generalFunctions as generalFunctions
import sifi21INTA.Sirna as Sirna

class SifiPipeline:
    def __init__(self, bowtieDB, queryFile, outputDir, mode=0, sirnaSize=21, mismatches=0, accessibilityCheck=True, accessibilityWindow=8, strandCheck=True, endCheck=True, endStabilityTreshold = 1.0, targetSiteAccessibilityTreshold=0.1, terminalCheck=True):

        # Parameters
        self.mode = mode                                                        # Mode, either RNAi design (0) or off-target prediction (1)
        self.sirnaSize = sirnaSize                                              # siRNA size
        self.mismatches = mismatches                                            # Allowed mismatches
        self.strandCheck = strandCheck                                          # Strand selection is enabled or disabled
        self.endCheck = endCheck                                                # End stability selection is enabled or disabled
        self.accessibilityCheck = accessibilityCheck                            # Target site accessibility is enabled or disabled
        self.accessibilityWindow = accessibilityWindow                          # Accessibility window
        self.endStabilityTreshold = endStabilityTreshold                        # End stability treshold
        self.tsAccessibilityTreshold = targetSiteAccessibilityTreshold          # Target site accessibility threshold
        self.terminalCheck = terminalCheck

        self.rnaplfoldLocation = "/usr/local/bin"                               # Rnaplfold path
        self.bowtieLocation = "/usr/bin"                                        # Bowtie path
        self.bowtieDB = bowtieDB                                                # Bowtie DB complete path
        self.allTargets = {}
        self.mainTargets = []
        self.outputDir = outputDir                                              # Outpur directory path
        self.queryFile = queryFile                                              # Query sequence in single fasta format complete path
        query = tuple(SeqIO.parse(queryFile, "fasta"))[0]
        self.queryName = str(query.id).replace(" ","_")
        self.querySequence = str(query.seq)
        self.sirnaFastaFile = self.outputDir+"/"+self.queryName+"."+str(self.sirnaSize)+"sirnas.fasta"
        self.sirnaData = {}
        self.jsonFileName = self.outputDir+"/"+self.queryName+".json"
        self.jsonList = []
        
        # Constants
        self.winsize = 80                                                       # Average the pair probabil. over windows of given size
        self.span = 40                                                          # Set the maximum allowed separation of a base pair to span
        self.temperature = 22                                                   # Temperature for calculation free energy
        self.startPosition = 0
        self.overhang = 2                                                       # siRNA overhang
        self.endNucleotides = 3                                                 # siRNA end nucleotides

    def runPipeline(self):
        # Create all siRNAs of size "sirna_size" and save them into multifasta and tab files
        self.createSirnas()
        tableData = ""
        if self.mode == 0:
            tableData = self.designPipeline()
        else:
            tableData = self.offTargetPipeline()
        return tableData

    def createSirnas(self):
        #Create siRNA's of size "sirna_size" of a sequence.
        # Slice over sequence and split into xmers.
        sirnaFastaFile = open(self.sirnaFastaFile, 'w')
        start = self.startPosition
        for start in range(start , len(self.querySequence)-self.sirnaSize+1):
            sequence = self.querySequence[start:start+self.sirnaSize]
            sequence.upper()
            self.sirnaData[start+1] = Sirna.Sirna(sequence)
            sirnaFastaFile.write('>' + str(start+1) + '\n')
            sirnaFastaFile.write(sequence + '\n')
        sirnaFastaFile.close()

    #TODO: Generar plot y diferentes salidas deseadas!!!!!
    def designPipeline(self):
        # Run BOWTIE against DB
        self.runBowtie()
        
        # Get main targets
        self.getMainTargets()
        
        # Run RNAplfold
        self.runRnaplfold()
        self.calculateAllSirnasEfficiency()
        
        # Load results to JSON format
        self.createJsonFile()
        
        print("Main target name:",self.mainTargets)
        
        resultTuple = self.getResultSummary()
        self.printResultTable(resultTuple)

        self.efficiencyFigure()

    def offTargetPipeline(self):
        # Run BOWTIE against DB
        self.runBowtie()

        # Load results to JSON format
        self.createJsonFile()

        resultTuple = self.getResultSummary()
        self.printResultTable(resultTuple)

    def runBowtie(self):
        """Run BOWTIE alignment."""
        bowtieFile = self.outputDir+"/"+self.queryName+".bowtiehit"
        os.chdir(self.bowtieLocation)
        process = subprocess.Popen(["bowtie", "-a", "-v", str(self.mismatches),  "-y", "-x", self.bowtieDB, "-f", self.sirnaFastaFile, bowtieFile])
        process.wait()
        if os.path.exists(bowtieFile):
            self.loadBowtieData(bowtieFile)

    def loadBowtieData(self, bowtieFile):
        """Converts Bowtie data into lists of list. Just for convenience."""
        bowtieDataFile = open(bowtieFile)
        for bowtieMatch in bowtieDataFile:
            bowtieDataSplit = bowtieMatch.strip().split('\t')
            sirnaName = int(bowtieDataSplit[0])
            hitName = bowtieDataSplit[2]

            # If we have missmatches, we will append a value for each entry.
            # Data for bowtie align: (strand, hit name, alignment pos, missmatches)
            if len(bowtieDataSplit) == 7:
                bowtieDataSplit = bowtieDataSplit[1:3]+[int(bowtieDataSplit[3]) , 0]
            else:
                bowtieDataSplit = bowtieDataSplit[1:3]+[int(bowtieDataSplit[3]) , int(bowtieDataSplit[7])]
            
            sirnaBowtieData = self.sirnaData[sirnaName].bowtieData
            if not self.sirnaData[sirnaName].bowtieData:
                self.sirnaData[sirnaName].bowtieData = []    
            self.sirnaData[sirnaName].bowtieData.append(tuple(bowtieDataSplit))
            if hitName not in self.allTargets:
                self.allTargets[hitName] = 1
            else:
                self.allTargets[hitName] += 1
        bowtieDataFile.close()

    def runRnaplfold(self):
        os.chdir(self.rnaplfoldLocation)
        seq = open(self.queryFile, 'r').read()
        cwd = self.outputDir+"/"+self.queryName+"_RNAplfold"
        os.mkdir(cwd)
        prc_stdout = subprocess.PIPE
        prc = subprocess.Popen(['RNAplfold', '-W', '%d'%self.winsize,'-L', '%d'% self.span, '-u', '%d'%self.sirnaSize, '-T', '%.2f'%self.temperature], stdin=subprocess.PIPE, stdout=prc_stdout, cwd=cwd)
        prc.stdin.write(seq.encode('utf-8'))
        prc.stdin.write('\n'.encode('utf-8'))
        prc.communicate()

        lunpFileName = cwd + '/' + self.queryName + '_lunp'
        if os.path.exists(lunpFileName):
            self.loadLunpData(lunpFileName)
        
    def loadLunpData(self, lunpFileName):
        lunpFile = open(lunpFileName)
        for lunpLine in lunpFile:
            if "#" not in lunpLine:
                lunpLine = lunpLine[:-1].split("\t")
                end = int(lunpLine[0])
                if end >= self.sirnaSize:
                    sirnaName = end-self.sirnaSize+1
                    self.sirnaData[sirnaName].lunpData = tuple(map(float, lunpLine[1:]))

    def calculateAllSirnasEfficiency(self):
        for sirnaName in self.sirnaData:
            sirna = self.sirnaData[sirnaName]
            if sirnaName < 3:
                sequenceN2 = None
            else:
                sequenceN2 = self.sirnaData[sirnaName-2].sequence
            sirna.calculateEfficiency(sequenceN2, self.accessibilityWindow, self.tsAccessibilityTreshold, self.endStabilityTreshold, self.startPosition, self.endNucleotides, self.overhang, self.terminalCheck, self.strandCheck, self.endCheck, self.accessibilityCheck)

    def getMainTargets(self):
        """ The user must choose the main targets from all bowtie hit names.
           All remaining hits will be marked as off-targets."""
        # TODO: Open popup to choose main target
        if self.allTargets:
            allTargetsData = sorted(self.allTargets.items(), key=lambda x: x[1], reverse=True)
            print(allTargetsData)
            mainTargetPos = input("Select main targets (comma separated): ").split(",")
            for posTarget in range(len(allTargetsData)):
                if str(posTarget+1) in mainTargetPos:
                    self.mainTargets.append(allTargetsData[posTarget][0])

    def createJsonFile(self):
        jsonFile = open(self.jsonFileName, "w")
        self.dataToJson()
        json.dump(self.jsonList, jsonFile, indent=4)
        jsonFile.close()
    
    def dataToJson(self):
        """Extracts the data from bowtie results and put everything into json format.
           Efficiency is calculated for each siRNA.
           If no target is found, for design mode the siRNA fasta file is used instead of Bowtie data."""
        for sirnaName in self.sirnaData:
            sirna = self.sirnaData[sirnaName]
            if sirna.bowtieData:
                for alignment in sirna.bowtieData:
                    hitName = alignment[1]
                    offTarget = True
                    if hitName in self.mainTargets:
                        offTarget = False
                    sirnaDict = {"query_name": self.queryName, "sirna_position": sirnaName}
                    sirnaDict.update(self.sirnaData[sirnaName].toDict())
                    sirnaDict.update({"is_off_target": offTarget,
                                      "hit_name": hitName, 
                                      "reference_strand_pos": alignment[2],
                                      "strand": alignment[0], 
                                      "mismatches": alignment[3]})
                    self.jsonList.append(sirnaDict)
            elif self.mode == 0:
                # Cuando no hay alignments, va solo la informacion de energia si es design
                sirnaDict = {"query_name": self.queryName, "sirna_position": sirnaName}
                sirnaDict.update(self.sirnaData[sirnaName].toDict())
                sirnaDict.update({"is_off_target": False})
                self.jsonList.append(sirnaDict)

    def getResultSummary(self):
        total = 0
        totalWithoutOff = 0
        efficient = 0
        efficientWithoutOff = 0
        for sirnaName in sorted(self.sirnaData.keys()):
            sirna = self.sirnaData[sirnaName]
            offCheck = sirna.haveOffTargets(self.mainTargets)
            effCheck = sirna.isEfficientCheck()
            if offCheck == False:
                totalWithoutOff += 1
            if effCheck:
                efficient += 1
            if effCheck and not offCheck:
                #print(sirnaName, sirna)
                efficientWithoutOff += 1
            total += 1
        if self.mode == 1:
            efficient = efficientWithoutOff = None
        return (total, totalWithoutOff, efficient, efficientWithoutOff)

    def printResultTable(self, resultTuple):
        print("| Total | Total without off targets | Efficients | Efficients without off targets |")
        print("|---------------------------------------------------------------------------------|")
        print("|  %s   |             %s            |    %s      |               %s               |" % (resultTuple))
        print("|---------------------------------------------------------------------------------|")

    def efficiencyFigure(self):
        queryLen = len(self.querySequence)
        pos = [int(i) for i in range(1,queryLen+1)]
        count = [0]*queryLen
        offRegions = []
        
        for sirnaName in sorted(self.sirnaData.keys()):
            start = sirnaName-1
            end = start + (self.sirnaSize-1)
            sirna = self.sirnaData[sirnaName]
            if sirna.isEfficientCheck():
                for sirnaPos in range(start, end):
                    count[sirnaPos]+=1
            if sirna.haveOffTargets(self.mainTargets):
                if offRegions and offRegions[-1][1] >= start:
                    lastRegion = offRegions.pop()
                    start = lastRegion[0]-1
                offRegions.append((start+1 , end+1))

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=pos, y=count,
                                 line_shape='hv', # Connect data points with lines
                                 #mode='lines', # Connect data points with lines
                                 name='siRNAs efficients')) # Name in the legend
        
        # Layout parameters
        fig.update_layout(
            title='siRNAs efficients per target position', # Title
            xaxis_title='Target position', # y-axis name
            yaxis_title='Number of efficient siRNAs', # x-axis name
            xaxis_tickangle=45,  # Set the x-axis label angle
            showlegend=True,     # Display the legend
        )
        #TODO: Hay que hacer un merge de las regiones que comparten posiciones para que sea una unica region
        # se podria hacer a medida que se van cargando las regiones, corriendo los extremos
        for start,end in offRegions:
            fig.add_vrect(x0=start, x1=end,
                      fillcolor="LightSalmon", opacity=0.5,
                      layer="below", line_width=0)

        # save this file as a standalong html file:
        fig.write_html(self.outputDir+"/"+self.queryName+".html")