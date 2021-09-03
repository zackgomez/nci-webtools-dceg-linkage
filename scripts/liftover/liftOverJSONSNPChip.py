# this script is written to use the liftOver program to convert SNPChip's snp_col MongoDB collection of arrays data with GRCh37 positions to GRCh38
# and create a MongoDB-importable JSON file with both GRCh37 and GRCh38 positions
# http://genome.ucsc.edu/goldenPath/help/hgTracksHelp.html#Liftover
# input: use "mongoexport" to export existing "snp_col" MongoDB collection to .json outfile and use as input for this script
# requirement: you must download liftOver precompiled executable binary and place in PATH before running script
# requirement: you must download and install tabix in local PATH before running script
# requirement: you must download desired liftOver chain file from https://hgdownload.cse.ucsc.edu/goldenpath/hg19/liftOver/

import time
import sys
import json
import datetime
import subprocess

currentDT = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
start_time = time.time()  # measure script's run time

def generateInputBed(inputJSONFile):
    print("Generating input BED file...")
    inputBedFileName = "input." + currentDT + ".bed"
    with open(inputBedFileName, 'a') as bedfile:
        with open(inputJSONFile) as inputfile:
            for line in inputfile:
                jsonObj = json.loads(line)
                if isinstance(jsonObj['position_grch37'], int):
                    writeLine = ["chr" + jsonObj['chromosome_grch37'], str(jsonObj['position_grch37']), str(int(jsonObj['position_grch37']) + 1), json.dumps(jsonObj).replace(" ", "")]
                    bedfile.write("\t".join(writeLine) + '\n')
    print("liftOver input file " + inputBedFileName + " generated...")
    return inputBedFileName

def runLiftOver(inputBedFileName, chainFile):
    print("Running liftOver...")
    outputBedFileName = "output." + currentDT + ".bed"
    outputUnmappedBedFileName = "output.unMapped." + currentDT + ".bed"
    # usage: liftOver oldFile map.chain newFile unMapped
    process = subprocess.Popen(['liftOver', inputBedFileName, chainFile, outputBedFileName, outputUnmappedBedFileName], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    print(stdout.decode('utf-8'), stderr.decode('utf-8'))
    print("liftOver output " + outputBedFileName + " generated...")
    print("liftOver unmapped output " + outputUnmappedBedFileName + " generated...")
    return outputBedFileName, outputUnmappedBedFileName

# CAPTURE END POS

def generateJSONFile(outputBedFileName, outputUnmappedBedFileName, outputJSONFile):
    print("Generating JSON file...")
    with open(outputJSONFile, 'a') as jf:
        with open(outputBedFileName) as bf:
            linesMapped = bf.read().splitlines() 
        splitLinesMapped = list(map(lambda x: x.split("\t"), linesMapped))
        with open(outputUnmappedBedFileName) as bf:
            linesUnmapped = bf.read().splitlines() 
        splitLinesUnmapped = list(map(lambda x: x.split("\t"), linesUnmapped))
    
        for line in splitLinesMapped:
            # drop any rows with chr#_*
            if len(line[0].split("_")) <= 1:
                writeJSONMapped = {
                    "score": line[3].split("__")[2],
                    "chromosome_grch37": line[3].split("__")[1].split(":")[0],
                    "position_grch37": int(line[3].split("__")[1].split(":")[1]),
                    "chromosome_grch38": line[0],
                    "position_grch38": int(line[2])
                }
                jf.write(json.dumps(writeJSONMapped) + "\n")
        for line in splitLinesUnmapped:
            if "#" not in line[0]:
                writeJSONUnmapped = {
                    "score": line[3].split("__")[2],
                    "chromosome_grch37": line[3].split("__")[1].split(":")[0],
                    "position_grch37": int(line[3].split("__")[1].split(":")[1]),
                    "chromosome_grch38": "NA",
                    "position_grch38": "NA"
                }
                jf.write(json.dumps(writeJSONUnmapped) + "\n")

def main():
    print("Starting liftOver script for SNPChip 'snp_col' JSON...")
    try:
        inputJSONFile = sys.argv[1]
        chainFile = sys.argv[2]
        outputJSONFile = sys.argv[3]
    except:
        print("USAGE: python3 liftOverJSONSNPChip.py <INPUT_JSON_DATA> <CHAIN_FILE> <OUTPUT_JSON_FILENAME_W_EXTENSION>")
        print("EXAMPLE: python3 liftOverJSONSNPChip.py ./export_snp_col.json ./hg19ToHg38.over.chain.gz new_snp_col.json")
        sys.exit(1)

    inputBedFileName = generateInputBed(inputJSONFile)
    outputBedFileName, outputUnmappedBedFileName = runLiftOver(inputBedFileName, chainFile)
    # generateJSONFile(outputBedFileName, outputUnmappedBedFileName, outputJSONFile)

    print("LiftOver completed [" + str(time.time() - start_time) + " seconds elapsed]...")

    
if __name__ == "__main__":
    main()